"""Deterministic, durable Notification intent creation and recovery."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from market_intelligence.db.models import (
    AuditLog,
    ContentItem,
    Notification,
    NotificationChannel,
    NotificationPriority,
    NotificationStatus,
)

POLICY_ID = "r1-unified-control-plane"


def notification_dedup_key(content_item_id: UUID) -> str:
    return f"telegram:content:{content_item_id}"


async def create_pending_intent(
    session: AsyncSession, content_item_id: UUID, *, now: datetime | None = None
) -> UUID | None:
    current = now or datetime.now(UTC)
    return await session.scalar(
        insert(Notification)
        .values(
            content_item_id=content_item_id,
            priority=NotificationPriority.P3,
            priority_reason="new_visible_content",
            policy_rule_id=POLICY_ID,
            policy_version="1",
            channel=NotificationChannel.TELEGRAM_PUSH,
            dedup_key=notification_dedup_key(content_item_id),
            payload_version=1,
            status=NotificationStatus.PENDING,
            scheduled_at=current,
            retry_count=0,
        )
        .on_conflict_do_nothing(index_elements=["dedup_key"])
        .returning(Notification.id)
    )


async def record_intent_recovery(session: AsyncSession, content_item_id: UUID) -> None:
    session.add(
        AuditLog(
            actor_type="system",
            actor_id=None,
            action="notification_intent_recovery",
            target_type="content_item",
            target_id=content_item_id,
            before=None,
            after={"safe_error": "notification_intent_missing"},
        )
    )


@dataclass(frozen=True, slots=True)
class ReconcileReport:
    scanned: int
    created: int
    resolved: int


class NotificationIntentReconciler:
    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory

    async def reconcile(self, watermark: datetime, *, limit: int = 100) -> ReconcileReport:
        created = resolved = 0
        async with self._factory.begin() as session:
            recoveries = tuple(
                await session.scalars(
                    select(AuditLog)
                    .where(AuditLog.action == "notification_intent_recovery")
                    .order_by(AuditLog.created_at, AuditLog.id)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            )
            seen: set[UUID] = set()
            for audit in recoveries:
                if audit.target_id is None:
                    continue
                already = await session.scalar(
                    select(AuditLog.id)
                    .where(
                        AuditLog.action == "notification_intent_recovery_resolved",
                        AuditLog.target_id == audit.target_id,
                    )
                    .limit(1)
                )
                if already is not None:
                    continue
                if await create_pending_intent(session, audit.target_id) is not None:
                    created += 1
                session.add(
                    AuditLog(
                        actor_type="system",
                        actor_id=None,
                        action="notification_intent_recovery_resolved",
                        target_type="content_item",
                        target_id=audit.target_id,
                        before=None,
                        after={"recovery_audit_id": str(audit.id)},
                    )
                )
                resolved += 1
                seen.add(audit.target_id)
            remaining = max(limit - len(recoveries), 0)
            candidates = tuple(
                await session.scalars(
                    select(ContentItem.id)
                    .where(ContentItem.created_at >= watermark, ~ContentItem.notifications.any())
                    .order_by(ContentItem.created_at, ContentItem.id)
                    .limit(remaining)
                )
            )
            for content_id in candidates:
                if content_id in seen:
                    continue
                if await create_pending_intent(session, content_id) is not None:
                    created += 1
            return ReconcileReport(len(recoveries) + len(candidates), created, resolved)
