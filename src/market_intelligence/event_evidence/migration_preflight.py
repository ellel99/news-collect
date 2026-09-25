"""Read-only, value-free existing-state preflight for migration 0011."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


async def validate_0011_pre_migration(engine: AsyncEngine) -> tuple[dict[str, Any], int]:
    checks = {
        "broken_active_association_count": """
          SELECT count(*) FROM event_candidate_evidence a
          LEFT JOIN event_candidates c ON c.id=a.event_candidate_id
          LEFT JOIN evidence_items e ON e.id=a.evidence_item_id
          WHERE a.active AND (c.id IS NULL OR e.id IS NULL OR a.removed_at IS NOT NULL)
        """,
        "active_without_rich_packet_count": """
          SELECT count(*) FROM event_candidate_evidence a
          WHERE a.active AND NOT EXISTS (
            SELECT 1 FROM evidence_projection_links l
            JOIN safe_fact_projections p ON p.id=l.safe_fact_projection_id
            WHERE l.evidence_item_id=a.evidence_item_id
              AND l.status='linked' AND p.processing_status='ready'
          )
        """,
    }
    counts: dict[str, int] = {}
    async with engine.connect() as connection:
        for name, statement in checks.items():
            counts[name] = int((await connection.execute(text(statement))).scalar_one())
    errors: list[str] = []
    if counts["broken_active_association_count"]:
        errors.append("migration_0011_active_association_invalid")
    if counts["active_without_rich_packet_count"]:
        errors.append("migration_0011_rich_packet_unavailable")
    report: dict[str, Any] = {
        "status": "PASS" if not errors else "BLOCKED",
        **counts,
        "safe_errors": errors,
        "migration_ready": not errors,
    }
    return report, 0 if not errors else 2
