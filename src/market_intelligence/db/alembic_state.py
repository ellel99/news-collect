"""Safe Alembic state diagnosis and guarded head-only repair."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.util.exc import CommandError
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from market_intelligence.db.base import Base

_LOCK_KEY = "market_intelligence_alembic_state_repair"


@dataclass(frozen=True, slots=True)
class MigrationInventory:
    revisions: tuple[str, ...]
    code_heads: tuple[str, ...]
    chain_intact: bool
    safe_errors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AlembicStateReport:
    status: str
    database_revision: str | None
    code_heads: tuple[str, ...]
    code_revisions: tuple[str, ...]
    database_revision_known_to_code: bool
    revision_chain_intact: bool
    schema_compatible_with_head: bool
    schema_difference_count: int
    normal_upgrade_available: bool
    repair_available: bool
    safe_errors: tuple[str, ...]

    def safe_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["code_heads"] = list(self.code_heads)
        value["code_revisions"] = list(self.code_revisions)
        value["safe_errors"] = list(self.safe_errors)
        return value


@dataclass(frozen=True, slots=True)
class AlembicRepairReport:
    status: str
    execute_requested: bool
    database_revision_before: str | None
    database_revision_after: str | None
    target_head: str | None
    schema_compatible_with_head: bool
    database_updated: bool
    safe_errors: tuple[str, ...]

    def safe_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["safe_errors"] = list(self.safe_errors)
        return value


def load_migration_inventory(project_root: Path) -> MigrationInventory:
    """Load the local revision graph without touching the database."""

    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "alembic"))
    try:
        scripts = ScriptDirectory.from_config(config)
        heads = tuple(sorted(scripts.get_heads()))
        revisions = tuple(sorted(revision.revision for revision in scripts.walk_revisions()))
    except (CommandError, OSError, KeyError, ValueError):
        return MigrationInventory((), (), False, ("alembic_revision_chain_broken",))
    errors: list[str] = []
    if len(heads) != 1:
        errors.append("alembic_head_not_unique")
    if not revisions:
        errors.append("alembic_revision_chain_empty")
    return MigrationInventory(revisions, heads, not errors, tuple(errors))


async def diagnose_alembic_state(
    connection: AsyncConnection,
    inventory: MigrationInventory,
) -> AlembicStateReport:
    database_revision = await _database_revision(connection)
    compatible, difference_count = await connection.run_sync(_schema_compatibility)
    return assess_alembic_state(database_revision, inventory, compatible, difference_count)


def assess_alembic_state(
    database_revision: str | None,
    inventory: MigrationInventory,
    schema_compatible: bool,
    difference_count: int,
) -> AlembicStateReport:
    known = database_revision is not None and database_revision in inventory.revisions
    errors = list(inventory.safe_errors)
    if database_revision is None:
        errors.append("alembic_database_revision_missing")
    elif not known:
        errors.append("alembic_database_revision_missing_from_code")
    if not schema_compatible:
        errors.append("alembic_schema_not_compatible_with_head")

    normal_upgrade_available = (
        known
        and inventory.chain_intact
        and len(inventory.code_heads) == 1
        and database_revision not in inventory.code_heads
    )
    if normal_upgrade_available:
        errors.append("alembic_normal_upgrade_required")
    repair_available = (
        database_revision is not None
        and not known
        and inventory.chain_intact
        and len(inventory.code_heads) == 1
        and schema_compatible
    )
    at_head = known and database_revision in inventory.code_heads
    status = "PASS" if at_head and inventory.chain_intact and schema_compatible else "BLOCKED"
    return AlembicStateReport(
        status=status,
        database_revision=database_revision,
        code_heads=inventory.code_heads,
        code_revisions=inventory.revisions,
        database_revision_known_to_code=known,
        revision_chain_intact=inventory.chain_intact,
        schema_compatible_with_head=schema_compatible,
        schema_difference_count=difference_count,
        normal_upgrade_available=normal_upgrade_available,
        repair_available=repair_available,
        safe_errors=tuple(dict.fromkeys(errors)),
    )


async def repair_alembic_state(
    engine: AsyncEngine,
    inventory: MigrationInventory,
    *,
    execute: bool,
) -> AlembicRepairReport:
    async with engine.begin() as connection:
        await connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": _LOCK_KEY}
        )
        diagnosis = await diagnose_alembic_state(connection, inventory)
        head = inventory.code_heads[0] if len(inventory.code_heads) == 1 else None
        if diagnosis.status == "PASS":
            return _repair_report("PASS", execute, diagnosis, head, False, ())
        if not diagnosis.repair_available or head is None:
            return _repair_report("BLOCKED", execute, diagnosis, head, False, diagnosis.safe_errors)
        if not execute:
            return _repair_report(
                "DRY_RUN",
                False,
                diagnosis,
                head,
                False,
                ("alembic_repair_requires_execute",),
            )

        result = await connection.execute(
            text(
                "UPDATE alembic_version SET version_num = :head "
                "WHERE version_num = :database_revision"
            ),
            {"head": head, "database_revision": diagnosis.database_revision},
        )
        if result.rowcount != 1:
            raise RuntimeError("alembic_version_concurrent_change")
        return AlembicRepairReport(
            status="REPAIRED",
            execute_requested=True,
            database_revision_before=diagnosis.database_revision,
            database_revision_after=head,
            target_head=head,
            schema_compatible_with_head=True,
            database_updated=True,
            safe_errors=(),
        )


def _repair_report(
    status: str,
    execute: bool,
    diagnosis: AlembicStateReport,
    head: str | None,
    updated: bool,
    errors: tuple[str, ...],
) -> AlembicRepairReport:
    return AlembicRepairReport(
        status=status,
        execute_requested=execute,
        database_revision_before=diagnosis.database_revision,
        database_revision_after=diagnosis.database_revision,
        target_head=head,
        schema_compatible_with_head=diagnosis.schema_compatible_with_head,
        database_updated=updated,
        safe_errors=errors,
    )


async def _database_revision(connection: AsyncConnection) -> str | None:
    exists = await connection.scalar(text("SELECT to_regclass('alembic_version') IS NOT NULL"))
    if not exists:
        return None
    rows = tuple(await connection.scalars(text("SELECT version_num FROM alembic_version")))
    return rows[0] if len(rows) == 1 else None


def _schema_compatibility(connection: Connection) -> tuple[bool, int]:
    context = MigrationContext.configure(
        connection,
        opts={"compare_type": True, "compare_server_default": True},
    )
    differences = compare_metadata(context, Base.metadata)
    return not differences, len(differences)
