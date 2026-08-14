"""Read-only shadow and guarded cutover/rollback audit tooling."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from market_intelligence.db.models import (
    CollectionCursor,
    CollectionRun,
    CollectionRunStatus,
    CollectionTarget,
    CollectionTargetStatus,
    ContentItem,
)


@dataclass(frozen=True, slots=True)
class AuthorityAudit:
    status: str
    active_targets: int
    running_runs: int
    unmapped_runs: int
    unmapped_cursors: int
    notification_gaps: int
    safe_errors: tuple[str, ...]


class ControlPlaneAuditService:
    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory

    async def shadow(self) -> AuthorityAudit:
        """Pure read-only comparison: never enqueue, request, or write."""
        async with self._factory() as session:
            active = int(
                await session.scalar(
                    select(func.count())
                    .select_from(CollectionTarget)
                    .where(CollectionTarget.status == CollectionTargetStatus.ACTIVE)
                )
                or 0
            )
            running = int(
                await session.scalar(
                    select(func.count())
                    .select_from(CollectionRun)
                    .where(CollectionRun.status == CollectionRunStatus.RUNNING)
                )
                or 0
            )
            unmapped_runs = int(
                await session.scalar(
                    select(func.count())
                    .select_from(CollectionRun)
                    .where(CollectionRun.target_id.is_(None))
                )
                or 0
            )
            unmapped_cursors = int(
                await session.scalar(
                    select(func.count())
                    .select_from(CollectionCursor)
                    .where(CollectionCursor.target_id.is_(None))
                )
                or 0
            )
            errors = tuple(
                code
                for condition, code in (
                    (running > 0, "collection_runs_still_running"),
                    (unmapped_runs > 0, "collection_runs_unmapped"),
                    (unmapped_cursors > 0, "collection_cursors_unmapped"),
                )
                if condition
            )
            return AuthorityAudit(
                "PASS" if not errors else "BLOCKED",
                active,
                running,
                unmapped_runs,
                unmapped_cursors,
                0,
                errors,
            )

    async def notification_gap_audit(self, watermark: datetime) -> AuthorityAudit:
        async with self._factory() as session:
            gaps = int(
                await session.scalar(
                    select(func.count())
                    .select_from(ContentItem)
                    .where(ContentItem.created_at >= watermark, ~ContentItem.notifications.any())
                )
                or 0
            )
        return AuthorityAudit(
            "PASS" if gaps == 0 else "BLOCKED",
            0,
            0,
            0,
            0,
            gaps,
            (() if gaps == 0 else ("notification_intent_gap",)),
        )


def cutover_watermark(now: datetime | None = None) -> datetime:
    """Return a watermark candidate without persisting or activating it."""
    return now or datetime.now(UTC)


def legacy_combined_task_retirement_plan() -> dict[str, object]:
    return {
        "legacy_task": "multi_provider.telegram.run",
        "replacement_collection": "collection.control_plane.dispatch",
        "replacement_delivery": "notification.telegram.deliver",
        "activation_performed": False,
    }
