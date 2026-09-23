#!/usr/bin/env python3
"""Value-free typed preflight required before applying Alembic revision 0010."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from market_intelligence.core.config import Settings
from market_intelligence.db.session import create_engine
from market_intelligence.providers.operation_policy import factual_operation_policy
from market_intelligence.safe_projection.contracts import (
    ProjectionContractError,
    canonical_projection_hash,
    normalize_and_classify_factual_payload,
)

_PAGE_SIZE = 500


async def validate(engine: AsyncEngine) -> tuple[dict[str, object], int]:
    checked = 0
    cursor: str | None = None
    safe_errors: set[str] = set()
    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            )
            while True:
                rows = (
                    (
                        await connection.execute(
                            text("""
                        SELECT p.id::text,p.provider,p.operation_key,
                               p.projection_schema_version,p.factual_payload,
                               p.projection_hash,p.quality_status::text,
                               o.provider_contract_version
                        FROM evidence_projection_links l
                        JOIN safe_fact_projections p ON p.id=l.safe_fact_projection_id
                        JOIN raw_item_observations o ON o.id=p.observation_id
                        WHERE l.status='linked'
                          AND (:cursor IS NULL OR p.id::text > :cursor)
                        ORDER BY p.id::text
                        LIMIT :limit
                        """),
                            {"cursor": cursor, "limit": _PAGE_SIZE},
                        )
                    )
                    .mappings()
                    .all()
                )
                if not rows:
                    break
                for row in rows:
                    checked += 1
                    try:
                        normalized, quality = normalize_and_classify_factual_payload(
                            row["provider"],
                            row["operation_key"],
                            row["projection_schema_version"],
                            row["factual_payload"],
                        )
                        factual_operation_policy(
                            row["provider"],
                            row["operation_key"],
                            row["provider_contract_version"],
                        )
                        if (
                            normalized != row["factual_payload"]
                            or canonical_projection_hash(normalized) != row["projection_hash"]
                            or quality != row["quality_status"]
                        ):
                            raise ProjectionContractError("projection_not_canonical")
                    except (ProjectionContractError, ValueError, TypeError):
                        safe_errors.add("migration_0010_projection_contract_invalid")
                cursor = rows[-1]["id"]
                if len(rows) < _PAGE_SIZE:
                    break
        except Exception:
            safe_errors.add("migration_0010_preflight_failed")
        finally:
            await transaction.rollback()
    report: dict[str, Any] = {
        "status": "PASS" if not safe_errors else "BLOCKED",
        "checked_linked_projection_count": checked,
        "safe_errors": sorted(safe_errors),
    }
    return report, 0 if not safe_errors else 2


async def run() -> tuple[dict[str, object], int]:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    engine = create_engine(settings)
    try:
        return await validate(engine)
    finally:
        await engine.dispose()


def main() -> int:
    report, exit_code = asyncio.run(run())
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
