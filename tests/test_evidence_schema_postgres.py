import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import insert, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from market_intelligence.db import Base, EvidenceItem

POSTGRES_TEST_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://market_intelligence:local_dev_only@localhost:5432/market_intelligence",
)

EXPECTED_COLUMNS = {
    "id",
    "evidence_version",
    "provider",
    "provider_item_type",
    "evidence_kind",
    "source_type",
    "source_id",
    "source_account_id",
    "raw_item_id",
    "content_item_id",
    "provider_item_id",
    "provider_item_hash",
    "event_time",
    "observed_at",
    "access_level",
    "processing_status",
    "official_source_flag",
    "market_data_flag",
    "disclosure_flag",
    "news_signal_flag",
    "content_presence",
    "numeric_presence",
    "entity_refs",
    "asset_refs",
    "topic_refs",
    "raw_payload_reference",
    "errors",
    "created_at",
    "updated_at",
}

FORBIDDEN_COLUMNS = {
    "title",
    "body",
    "url",
    "snippet",
    "description",
    "quote_value",
    "eia_value",
    "accession_number",
    "primary_document",
    "raw_payload",
    "response_body",
    "api_key",
    "token",
    "authorization",
    "embedding",
    "event_id",
    "analysis_id",
    "recommendation",
    "portfolio",
    "holding",
    "investment_plan",
}


@pytest_asyncio.fixture
async def evidence_connection() -> AsyncIterator[AsyncConnection]:
    schema = f"spec_0021_test_{uuid.uuid4().hex}"
    engine = create_async_engine(
        POSTGRES_TEST_URL,
        connect_args={"server_settings": {"search_path": schema}},
    )
    async with engine.connect() as setup_connection:
        await setup_connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        await setup_connection.commit()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        async with engine.connect() as connection:
            yield connection
    finally:
        async with engine.connect() as cleanup_connection:
            await cleanup_connection.rollback()
            await cleanup_connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            await cleanup_connection.commit()
        await engine.dispose()


@pytest.mark.asyncio
async def test_evidence_migration_upgrade_downgrade_reupgrade() -> None:
    schema = f"spec_0021_migration_{uuid.uuid4().hex}"
    engine = create_async_engine(
        POSTGRES_TEST_URL,
        connect_args={"server_settings": {"search_path": schema}},
    )
    async with engine.connect() as setup_connection:
        await setup_connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        await setup_connection.commit()

    def run_round_trip(sync_connection: Any) -> None:
        excluded_tables = {
            "evidence_items",
            "evidence_projection_links",
            "event_candidates",
            "event_candidate_evidence",
        }
        existing_tables = [
            table for table in Base.metadata.sorted_tables if table.name not in excluded_tables
        ]
        Base.metadata.create_all(sync_connection, tables=existing_tables)
        sync_connection.execute(text("DROP INDEX uq_raw_items_id_source_id"))
        revision = ScriptDirectory.from_config(Config("alembic.ini")).get_revision("0003")
        assert revision is not None
        assert revision.down_revision == "0002"
        module = revision.module
        with Operations.context(MigrationContext.configure(sync_connection)):
            module.upgrade()
            assert inspect(sync_connection).has_table("evidence_items")
            assert "uq_raw_items_id_source_id" in {
                index["name"] for index in inspect(sync_connection).get_indexes("raw_items")
            }
            module.downgrade()
            assert not inspect(sync_connection).has_table("evidence_items")
            assert "uq_raw_items_id_source_id" not in {
                index["name"] for index in inspect(sync_connection).get_indexes("raw_items")
            }
            module.upgrade()
            assert inspect(sync_connection).has_table("evidence_items")

    try:
        async with engine.begin() as connection:
            await connection.run_sync(run_round_trip)
    finally:
        async with engine.connect() as cleanup_connection:
            await cleanup_connection.rollback()
            await cleanup_connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            await cleanup_connection.commit()
        await engine.dispose()


async def provenance_fixture(connection: AsyncConnection) -> dict[str, Any]:
    now = datetime.now(UTC)
    source_id = (
        await connection.execute(
            text(
                """
                INSERT INTO sources (
                    code, name, source_type, access_method, authorization_status,
                    retention_class
                ) VALUES (
                    :code, 'Evidence Fixture', 'api', 'fake', 'authorized', 'metadata_only'
                ) RETURNING id
                """
            ),
            {"code": f"evidence-{uuid.uuid4().hex}"},
        )
    ).scalar_one()
    run_id = (
        await connection.execute(
            text(
                """
                INSERT INTO collection_runs (source_id, started_at, status)
                VALUES (:source_id, :now, 'succeeded') RETURNING id
                """
            ),
            {"source_id": source_id, "now": now},
        )
    ).scalar_one()
    raw_item_id = (
        await connection.execute(
            text(
                """
                INSERT INTO raw_items (
                    source_id, collection_run_id, fetched_at, retention_class, parse_status
                ) VALUES (
                    :source_id, :run_id, :now, 'metadata_only', 'parsed'
                ) RETURNING id
                """
            ),
            {"source_id": source_id, "run_id": run_id, "now": now},
        )
    ).scalar_one()
    return {"source_id": source_id, "raw_item_id": raw_item_id, "observed_at": now}


def valid_evidence(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "evidence_version": 1,
        "provider": "finnhub",
        "provider_item_type": "finnhub_quote",
        "evidence_kind": "market_data",
        "source_type": "market_data",
        "provider_item_id": None,
        "provider_item_hash": uuid.uuid4().hex + uuid.uuid4().hex,
        "access_level": "unknown",
        "processing_status": "pending",
        "official_source_flag": False,
        "market_data_flag": True,
        "disclosure_flag": False,
        "news_signal_flag": False,
        "content_presence": {},
        "numeric_presence": {},
        "entity_refs": [],
        "asset_refs": [],
        "topic_refs": [],
        "errors": [],
    }
    values.update(overrides)
    return values


async def insert_evidence(connection: AsyncConnection, values: dict[str, Any]) -> uuid.UUID:
    return (
        await connection.execute(
            insert(EvidenceItem.__table__).values(**values).returning(EvidenceItem.id)
        )
    ).scalar_one()


async def complete_values(connection: AsyncConnection, **overrides: Any) -> dict[str, Any]:
    values = {
        **await provenance_fixture(connection),
        "source_account_id": None,
        "content_item_id": None,
        "event_time": None,
        "raw_payload_reference": None,
        **overrides,
    }
    return valid_evidence(**values)


@pytest.mark.asyncio
async def test_evidence_table_columns_and_nullability(
    evidence_connection: AsyncConnection,
) -> None:
    def inspect_table(sync_connection: Any) -> tuple[set[str], dict[str, bool]]:
        columns = inspect(sync_connection).get_columns("evidence_items")
        return {column["name"] for column in columns}, {
            column["name"]: column["nullable"] for column in columns
        }

    columns, nullable = await evidence_connection.run_sync(inspect_table)
    assert columns == EXPECTED_COLUMNS
    assert columns.isdisjoint(FORBIDDEN_COLUMNS)
    assert nullable["raw_item_id"] is False
    assert nullable["content_item_id"] is True
    assert set(EvidenceItem.__table__.columns.keys()) == EXPECTED_COLUMNS
    assert any(
        {element.parent.name for element in constraint.elements} == {"raw_item_id", "source_id"}
        and {element.column.table.name for element in constraint.elements} == {"raw_items"}
        for constraint in EvidenceItem.__table__.foreign_key_constraints
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["finnhub", "eia"])
async def test_non_content_evidence_accepts_null_content_item(
    evidence_connection: AsyncConnection, provider: str
) -> None:
    overrides = (
        {}
        if provider == "finnhub"
        else {
            "provider": "eia",
            "provider_item_type": "eia_energy_timeseries",
            "evidence_kind": "energy_official",
            "source_type": "official_energy",
            "official_source_flag": True,
            "market_data_flag": False,
        }
    )
    values = await complete_values(evidence_connection, **overrides)
    assert await insert_evidence(evidence_connection, values)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("provider", "unknown_provider"),
        ("provider_item_type", "unknown_type"),
        ("evidence_kind", "analysis"),
        ("source_type", "event"),
        ("access_level", "unrestricted"),
        ("processing_status", "complete"),
        ("provider_item_hash", "ABC"),
        ("evidence_version", 0),
    ],
)
async def test_allowlists_and_scalar_checks_fail_closed(
    evidence_connection: AsyncConnection, field: str, invalid_value: Any
) -> None:
    values = await complete_values(evidence_connection, **{field: invalid_value})
    with pytest.raises(IntegrityError):
        await insert_evidence(evidence_connection, values)


@pytest.mark.asyncio
async def test_flags_consistency_is_enforced(evidence_connection: AsyncConnection) -> None:
    values = await complete_values(evidence_connection, news_signal_flag=True)
    with pytest.raises(IntegrityError):
        await insert_evidence(evidence_connection, values)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "invalid_json"),
    [
        ("content_presence", "[]"),
        ("numeric_presence", "[]"),
        ("entity_refs", "{}"),
        ("asset_refs", "{}"),
        ("topic_refs", "{}"),
        ("errors", "{}"),
    ],
)
async def test_jsonb_shapes_are_enforced(
    evidence_connection: AsyncConnection, field: str, invalid_json: str
) -> None:
    values = await complete_values(evidence_connection, **{field: invalid_json})
    with pytest.raises(IntegrityError):
        await insert_evidence(evidence_connection, values)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reference",
    ["https://example.invalid/raw", "http://example.invalid/raw", "file:///tmp/raw"],
)
async def test_external_raw_payload_references_are_rejected(
    evidence_connection: AsyncConnection, reference: str
) -> None:
    values = await complete_values(evidence_connection, raw_payload_reference=reference)
    with pytest.raises(IntegrityError):
        await insert_evidence(evidence_connection, values)


@pytest.mark.asyncio
async def test_internal_raw_payload_reference_is_accepted(
    evidence_connection: AsyncConnection,
) -> None:
    values = await complete_values(
        evidence_connection, raw_payload_reference="internal://safe/reference"
    )
    assert await insert_evidence(evidence_connection, values)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reference",
    [
        "internal://capture/api_key=secret",
        "capture://provider/api_token=secret",
        "local-ref://provider/token=secret",
        "internal://authorization=secret",
        "internal://x-finnhub-token=secret",
    ],
)
async def test_raw_payload_reference_secret_markers_are_rejected(
    evidence_connection: AsyncConnection, reference: str
) -> None:
    values = await complete_values(evidence_connection, raw_payload_reference=reference)
    with pytest.raises(IntegrityError):
        await insert_evidence(evidence_connection, values)


@pytest.mark.asyncio
async def test_provider_hash_unique_index(evidence_connection: AsyncConnection) -> None:
    item_hash = "a" * 64
    first = await complete_values(evidence_connection, provider_item_hash=item_hash)
    await insert_evidence(evidence_connection, first)
    await evidence_connection.commit()
    second = await complete_values(evidence_connection, provider_item_hash=item_hash)
    with pytest.raises(IntegrityError):
        await insert_evidence(evidence_connection, second)


@pytest.mark.asyncio
async def test_provider_item_id_partial_unique_index(evidence_connection: AsyncConnection) -> None:
    first = await complete_values(evidence_connection, provider_item_id="opaque-1")
    await insert_evidence(evidence_connection, first)
    await evidence_connection.commit()
    second = await complete_values(evidence_connection, provider_item_id="opaque-1")
    with pytest.raises(IntegrityError):
        await insert_evidence(evidence_connection, second)


@pytest.mark.asyncio
async def test_raw_item_foreign_key_is_enforced(evidence_connection: AsyncConnection) -> None:
    values = await complete_values(evidence_connection, raw_item_id=uuid.uuid4())
    with pytest.raises(IntegrityError):
        await insert_evidence(evidence_connection, values)


@pytest.mark.asyncio
async def test_raw_item_and_source_provenance_must_match(
    evidence_connection: AsyncConnection,
) -> None:
    matching = await complete_values(evidence_connection)
    assert await insert_evidence(evidence_connection, matching)
    await evidence_connection.commit()

    mismatched = await complete_values(evidence_connection)
    other_provenance = await provenance_fixture(evidence_connection)
    mismatched["raw_item_id"] = other_provenance["raw_item_id"]
    with pytest.raises(IntegrityError):
        await insert_evidence(evidence_connection, mismatched)
