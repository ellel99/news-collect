"""Delivery-only Telegram state machine backed exclusively by Notification rows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from market_intelligence.db.models import (
    Notification,
    NotificationChannel,
    NotificationStatus,
)
from market_intelligence.feed.provider_feed import ProviderFeedService
from market_intelligence.notifications.intent import POLICY_ID
from market_intelligence.scheduler.multi_provider import ProviderTelegramFormatter
from market_intelligence.telegram.manual_push import TelegramRuntimeCredential, TelegramTransport

MAX_ATTEMPTS = 5


@dataclass(frozen=True, slots=True)
class DeliveryReport:
    status: str
    sent: int = 0
    failed: int = 0
    safe_errors: tuple[str, ...] = ()


class NotificationDeliveryService:
    def __init__(
        self, factory: async_sessionmaker[AsyncSession], transport: TelegramTransport
    ) -> None:
        self._factory, self._transport = factory, transport
        self._feed, self._formatter = ProviderFeedService(factory), ProviderTelegramFormatter()

    async def deliver(
        self, credential: TelegramRuntimeCredential | None, *, limit: int = 20
    ) -> DeliveryReport:
        if not 1 <= limit <= 500:
            raise ValueError("notification_delivery_limit_invalid")
        if credential is None:
            return DeliveryReport("BLOCKED", safe_errors=("telegram_runtime_credential_missing",))
        rows = await self._claim(limit)
        sent = failed = 0
        errors: list[str] = []
        for notification_id, content_id in rows:
            items = await self._feed.by_content_ids((content_id,) if content_id else ())
            if len(items) != 1:
                await self._fail(notification_id, "notification_content_missing")
                failed += 1
                errors.append("notification_content_missing")
                continue
            try:
                message = self._formatter.format(items)
                response = await self._transport.send(credential, message)
                if not 200 <= response.status_code < 300:
                    raise RuntimeError("telegram_request_rejected")
            except Exception:
                await self._fail(notification_id, "telegram_delivery_failed")
                failed += 1
                errors.append("telegram_delivery_failed")
                continue
            await self._sent(notification_id)
            sent += 1
        status = "PASS" if not failed else "PARTIAL" if sent else "FAILED"
        if not rows:
            status = "NO_NEW_ITEMS"
        return DeliveryReport(status, sent, failed, tuple(sorted(set(errors))))

    async def _claim(self, limit: int) -> tuple[tuple[UUID, UUID | None], ...]:
        now, stale = datetime.now(UTC), datetime.now(UTC) - timedelta(minutes=10)
        async with self._factory.begin() as session:
            rows = tuple(
                await session.scalars(
                    select(Notification)
                    .where(
                        Notification.policy_rule_id == POLICY_ID,
                        Notification.channel == NotificationChannel.TELEGRAM_PUSH,
                        Notification.retry_count < MAX_ATTEMPTS,
                        Notification.scheduled_at <= now,
                        or_(
                            Notification.status == NotificationStatus.PENDING,
                            Notification.status == NotificationStatus.FAILED,
                            (Notification.status == NotificationStatus.SENDING)
                            & (Notification.scheduled_at < stale),
                        ),
                    )
                    .order_by(Notification.scheduled_at, Notification.id)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            )
            claimed = []
            for row in rows:
                row.status = NotificationStatus.SENDING
                row.scheduled_at = now
                claimed.append((row.id, row.content_item_id))
            return tuple(claimed)

    async def _fail(self, notification_id: UUID, code: str) -> None:
        async with self._factory.begin() as session:
            row = await session.get(Notification, notification_id, with_for_update=True)
            if row is None or row.status is not NotificationStatus.SENDING:
                return
            row.retry_count += 1
            row.status = NotificationStatus.FAILED
            row.failure_code = code
            delay = min(30 * (2 ** max(row.retry_count - 1, 0)), 3600)
            row.scheduled_at = datetime.now(UTC) + timedelta(seconds=delay)

    async def _sent(self, notification_id: UUID) -> None:
        async with self._factory.begin() as session:
            row = await session.get(Notification, notification_id, with_for_update=True)
            if row is None or row.status is not NotificationStatus.SENDING:
                return
            row.status = NotificationStatus.SENT
            row.failure_code = None
            row.sent_at = datetime.now(UTC)
