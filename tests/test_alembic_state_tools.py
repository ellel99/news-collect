import inspect
import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from market_intelligence.db import Base
from market_intelligence.db.alembic_state import (
    MigrationInventory,
    assess_alembic_state,
    diagnose_alembic_state,
    load_migration_inventory,
    repair_alembic_state,
)

POSTGRES_TEST_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://market_intelligence:local_dev_only@localhost:5432/market_intelligence",
)


@pytest_asyncio.fixture
async def compatible_database() -> AsyncIterator[AsyncEngine]:
    schema = f"spec_0034_alembic_{uuid.uuid4().hex}"
    engine = create_async_engine(
        POSTGRES_TEST_URL,
        connect_args={"server_settings": {"search_path": schema}},
    )
    async with engine.connect() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        await connection.commit()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.execute(text("CREATE TABLE alembic_version (version_num varchar(32))"))
        await connection.execute(
            text("INSERT INTO alembic_version (version_num) VALUES ('legacy-0003')")
        )
    try:
        yield engine
    finally:
        async with engine.connect() as connection:
            await connection.rollback()
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            await connection.commit()
        await engine.dispose()


def inventory() -> MigrationInventory:
    return MigrationInventory(("0001", "0002", "0003", "0004", "0005"), ("0005",), True)


def test_database_revision_at_head_passes() -> None:
    report = assess_alembic_state("0005", inventory(), True, 0)

    assert report.status == "PASS"
    assert report.database_revision_known_to_code is True
    assert report.repair_available is False
    assert report.normal_upgrade_available is False
    assert report.safe_errors == ()


def test_missing_0003_fails_closed_with_safe_error() -> None:
    old_image = MigrationInventory(("0001", "0002"), ("0002",), True)

    report = assess_alembic_state("0003", old_image, False, 1)

    assert report.status == "BLOCKED"
    assert report.database_revision_known_to_code is False
    assert report.repair_available is False
    assert "alembic_database_revision_missing_from_code" in report.safe_errors


def test_known_prior_revision_requires_normal_upgrade_not_repair() -> None:
    report = assess_alembic_state("0003", inventory(), False, 3)

    assert report.status == "BLOCKED"
    assert report.normal_upgrade_available is True
    assert report.repair_available is False
    assert "alembic_normal_upgrade_required" in report.safe_errors


def test_broken_revision_chain_blocks_repair() -> None:
    broken = MigrationInventory((), (), False, ("alembic_revision_chain_broken",))

    report = assess_alembic_state("0003", broken, True, 0)

    assert report.status == "BLOCKED"
    assert report.repair_available is False
    assert "alembic_revision_chain_broken" in report.safe_errors


def test_repository_inventory_is_linear_and_contains_0003() -> None:
    project_root = Path(__file__).resolve().parents[1]

    report = load_migration_inventory(project_root)

    assert report.chain_intact is True
    assert report.revisions == ("0001", "0002", "0003", "0004", "0005")
    assert report.code_heads == ("0005",)


@pytest.mark.asyncio
async def test_0004_reconciles_legacy_0003_constraints(
    compatible_database: AsyncEngine,
) -> None:
    def reconcile(sync_connection: object) -> None:
        connection = sync_connection  # SQLAlchemy sync connection supplied by run_sync.
        connection.execute(
            text("ALTER TABLE evidence_items DROP CONSTRAINT fk_evidence_items_raw_item_source")
        )
        connection.execute(
            text(
                "ALTER TABLE evidence_items ADD CONSTRAINT evidence_items_raw_item_id_fkey "
                "FOREIGN KEY (raw_item_id) REFERENCES raw_items(id) ON DELETE RESTRICT"
            )
        )
        connection.execute(text("DROP INDEX uq_raw_items_id_source_id"))
        connection.execute(
            text(
                "ALTER TABLE evidence_items DROP CONSTRAINT "
                "ck_evidence_items_raw_payload_reference_no_secret_markers"
            )
        )
        revision = ScriptDirectory.from_config(Config("alembic.ini")).get_revision("0004")
        assert revision is not None
        with Operations.context(MigrationContext.configure(connection)):
            revision.module.upgrade()
            revision.module.upgrade()
        inspector = sa_inspect(connection)
        assert "uq_raw_items_id_source_id" in {
            item["name"] for item in inspector.get_indexes("raw_items")
        }
        assert "fk_evidence_items_raw_item_source" in {
            item["name"] for item in inspector.get_foreign_keys("evidence_items")
        }
        assert "ck_evidence_items_raw_payload_reference_no_secret_markers" in {
            item["name"] for item in inspector.get_check_constraints("evidence_items")
        }

    async with compatible_database.begin() as connection:
        await connection.run_sync(reconcile)


@pytest.mark.asyncio
async def test_dry_run_does_not_modify_compatible_database(
    compatible_database: AsyncEngine,
) -> None:
    report = await repair_alembic_state(compatible_database, inventory(), execute=False)

    async with compatible_database.connect() as connection:
        version = await connection.scalar(text("SELECT version_num FROM alembic_version"))
    assert report.status == "DRY_RUN"
    assert report.database_updated is False
    assert report.target_head == "0005"
    assert version == "legacy-0003"


@pytest.mark.asyncio
async def test_execute_repairs_only_compatible_database_to_code_head(
    compatible_database: AsyncEngine,
) -> None:
    report = await repair_alembic_state(compatible_database, inventory(), execute=True)

    async with compatible_database.connect() as connection:
        version = await connection.scalar(text("SELECT version_num FROM alembic_version"))
    assert report.status == "REPAIRED"
    assert report.database_updated is True
    assert report.database_revision_before == "legacy-0003"
    assert report.database_revision_after == "0005"
    assert version == "0005"


@pytest.mark.asyncio
async def test_incompatible_schema_blocks_execute_without_update(
    compatible_database: AsyncEngine,
) -> None:
    async with compatible_database.begin() as connection:
        await connection.execute(text("DROP TABLE event_candidate_evidence"))
        await connection.execute(text("DROP TABLE event_candidates"))
        await connection.execute(text("DROP TABLE evidence_items"))

    report = await repair_alembic_state(compatible_database, inventory(), execute=True)

    async with compatible_database.connect() as connection:
        version = await connection.scalar(text("SELECT version_num FROM alembic_version"))
    assert report.status == "BLOCKED"
    assert report.database_updated is False
    assert "alembic_schema_not_compatible_with_head" in report.safe_errors
    assert version == "legacy-0003"


@pytest.mark.asyncio
async def test_doctor_reports_schema_shape_without_sensitive_configuration(
    compatible_database: AsyncEngine,
) -> None:
    async with compatible_database.connect() as connection:
        report = await diagnose_alembic_state(connection, inventory())

    rendered = str(report.safe_dict()).lower()
    assert report.repair_available is True
    assert "database_url" not in rendered
    assert "local_dev_only" not in rendered
    assert "password" not in rendered
    assert "token" not in rendered
    assert ".env" not in rendered


def test_tools_have_no_forbidden_runtime_dependencies() -> None:
    import market_intelligence.db.alembic_state as module

    source = inspect.getsource(module).lower()
    forbidden = (
        "scheduler",
        "openai",
        "recommendation",
        "dedup",
        "event",
        "marketaux",
        "telegram",
        "provider_capture",
        "local_evaluation",
    )
    assert all(term not in source for term in forbidden)
