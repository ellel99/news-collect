"""Delivery-only Telegram worker backed exclusively by Notification rows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from market_intelligence.db.models import Notification, NotificationStatus
from market_intelligence.feed.provider_feed import ProviderFeedService
from market_intelligence.scheduler.multi_provider import ProviderTelegramFormatter
from market_intelligence.telegram.manual_push import TelegramRuntimeCredential, TelegramTransport


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
        if credential is None:
            return DeliveryReport("BLOCKED", safe_errors=("telegram_runtime_credential_missing",))
        now, stale = datetime.now(UTC), datetime.now(UTC) - timedelta(minutes=10)
        async with self._factory.begin() as session:
            rows = tuple(
                await session.scalars(
                    select(Notification)
                    .where(
                        Notification.retry_count < 5,
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
            content_ids = tuple(
                row.content_item_id for row in rows if row.content_item_id is not None
            )
            for row in rows:
                row.status = NotificationStatus.SENDING
                row.scheduled_at = now
        items = await self._feed.by_content_ids(content_ids)
        if not items:
            return DeliveryReport("NO_NEW_ITEMS")
        try:
            response = await self._transport.send(credential, self._formatter.format(items))
            if not 200 <= response.status_code < 300:
                raise RuntimeError("telegram_request_rejected")
        except (RuntimeError, ValueError):
            async with self._factory.begin() as session:
                for row in await session.scalars(
                    select(Notification)
                    .where(Notification.id.in_(tuple(r.id for r in rows)))
                    .with_for_update()
                ):
                    row.status = NotificationStatus.FAILED
                    row.failure_code = "telegram_delivery_failed"
                    row.retry_count += 1
            return DeliveryReport(
                "FAILED", failed=len(items), safe_errors=("telegram_delivery_failed",)
            )
        async with self._factory.begin() as session:
            for row in await session.scalars(
                select(Notification)
                .where(Notification.id.in_(tuple(r.id for r in rows)))
                .with_for_update()
            ):
                row.status = NotificationStatus.SENT
                row.failure_code = None
                row.sent_at = datetime.now(UTC)
        return DeliveryReport("PASS", sent=len(items))
