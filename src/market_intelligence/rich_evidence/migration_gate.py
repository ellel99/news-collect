"""Controlled, value-free deployment gate for Alembic revision 0010."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from alembic.config import Config
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from market_intelligence.rich_evidence.migration_preflight import validate_0010_pre_migration

_LOCK_KEY = "m2b_0010_controlled_upgrade"


@dataclass(frozen=True, slots=True)
class MigrationGateReport:
    status: str
    database_revision: str | None
    checked_linked_projection_count: int
    migration_executed: bool
    safe_errors: tuple[str, ...]


async def controlled_upgrade_0010(
    engine: AsyncEngine, *, execute: bool, writers_stopped: bool
) -> MigrationGateReport:
    """Validate 0009 and apply 0010 in one writer-blocking transaction."""
    errors: list[str] = []
    checked = 0
    async with engine.connect() as connection:
        locked = bool(
            await connection.scalar(
                text("SELECT pg_try_advisory_lock(hashtextextended(:key,0))"), {"key": _LOCK_KEY}
            )
        )
        if not locked:
            return MigrationGateReport(
                "BLOCKED", None, 0, False, ("migration_0010_maintenance_lock_unavailable",)
            )
        try:
            await connection.commit()
            async with connection.begin():
                revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
                if revision != "0009":
                    errors.append("migration_0010_database_revision_invalid")
                if not writers_stopped:
                    errors.append("migration_0010_writers_not_stopped")
                running = int(
                    await connection.scalar(
                        text("SELECT count(*) FROM collection_runs WHERE status='running'")
                    )
                    or 0
                )
                if running:
                    errors.append("migration_0010_running_writer_state_present")
                if not errors:
                    await connection.execute(
                        text("""
                        LOCK TABLE collection_runs, raw_items, raw_item_observations,
                          safe_fact_projections, evidence_projection_links,
                          evidence_items, content_items, sources
                        IN SHARE ROW EXCLUSIVE MODE
                        """)
                    )
                before = await _state_fingerprint(connection)
                if not errors:
                    report, code = await validate_0010_pre_migration(engine)
                    checked = cast(int, report["checked_linked_projection_count"])
                    errors.extend(str(value) for value in cast(list[str], report["safe_errors"]))
                    if code:
                        errors.append("migration_0010_typed_preflight_blocked")
                after = await _state_fingerprint(connection)
                if before != after:
                    errors.append("migration_0010_state_changed_after_preflight")
                if errors or not execute:
                    return MigrationGateReport(
                        "BLOCKED" if errors else "DRY_RUN",
                        str(revision) if revision is not None else None,
                        checked,
                        False,
                        tuple(sorted(set(errors))),
                    )
                await connection.run_sync(_apply_0010)
                await connection.execute(
                    text("UPDATE alembic_version SET version_num='0010' WHERE version_num='0009'")
                )
                final_revision = await connection.scalar(
                    text("SELECT version_num FROM alembic_version")
                )
                if final_revision != "0010":
                    errors.append("migration_0010_upgrade_not_applied")
                return MigrationGateReport(
                    "PASS" if not errors else "BLOCKED",
                    str(final_revision) if final_revision is not None else None,
                    checked,
                    not errors,
                    tuple(sorted(set(errors))),
                )
        except Exception:
            return MigrationGateReport(
                "BLOCKED",
                None,
                checked,
                False,
                ("migration_0010_controlled_upgrade_failed",),
            )
        finally:
            await connection.execute(
                text("SELECT pg_advisory_unlock(hashtextextended(:key,0))"), {"key": _LOCK_KEY}
            )
            await connection.commit()


async def _state_fingerprint(connection: AsyncConnection) -> str:
    value = await connection.scalar(
        text("""
        SELECT jsonb_build_object(
          'projection_count',(SELECT count(*) FROM safe_fact_projections),
          'projection_max',(SELECT max(updated_at)::text FROM safe_fact_projections),
          'link_count',(SELECT count(*) FROM evidence_projection_links),
          'link_max',(SELECT max(updated_at)::text FROM evidence_projection_links),
          'observation_count',(SELECT count(*) FROM raw_item_observations),
          'raw_count',(SELECT count(*) FROM raw_items),
          'evidence_count',(SELECT count(*) FROM evidence_items),
          'evidence_max',(SELECT max(updated_at)::text FROM evidence_items),
          'content_count',(SELECT count(*) FROM content_items),
          'content_max',(SELECT max(updated_at)::text FROM content_items)
        )::text
        """)
    )
    return str(value)


def _apply_0010(connection: Connection) -> None:
    revision = ScriptDirectory.from_config(Config("alembic.ini")).get_revision("0010")
    with Operations.context(MigrationContext.configure(connection)):
        revision.module.upgrade()
