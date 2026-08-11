"""Isolated multi-provider collection and reliable Telegram delivery orchestration."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol

from sqlalchemy import case, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from market_intelligence.db.models import (
    Notification,
    NotificationChannel,
    NotificationPriority,
    NotificationStatus,
)
from market_intelligence.feed.provider_feed import ProviderDisplayItem, ProviderFeedService
from market_intelligence.scheduler.marketaux_telegram import (
    MAX_DELIVERY_ATTEMPTS,
    SENDING_STALE_AFTER_SECONDS,
)
from market_intelligence.telegram.manual_push import TelegramRuntimeCredential, TelegramTransport

PROVIDER_ORDER = ("marketaux", "finnhub", "eia", "sec_edgar")
POLICY_ID = "spec-0038-multi-provider-telegram"


class ProviderScheduleStatus(StrEnum):
    PASS = "PASS"
    NO_NEW_ITEMS = "NO_NEW_ITEMS"
    RETRY = "RETRY"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True, slots=True)
class ProviderCycleResult:
    provider: str
    status: ProviderScheduleStatus
    collection_status: str
    raw_item_count: int = 0
    evidence_item_count: int = 0
    content_item_count: int = 0
    items: tuple[ProviderDisplayItem, ...] = ()
    safe_errors: tuple[str, ...] = ()
    retry_delay_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class ProviderScheduleReport:
    provider: str
    status: str
    collection_status: str
    raw_item_count: int
    evidence_item_count: int
    content_item_count: int
    sent_count: int
    failed_count: int
    safe_errors: tuple[str, ...] = ()
    delivery_status: str = "NO_NEW_ITEMS"
    delivery_safe_errors: tuple[str, ...] = ()

    def safe_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["safe_errors"] = list(self.safe_errors)
        value["delivery_safe_errors"] = list(self.delivery_safe_errors)
        return value


@dataclass(frozen=True, slots=True)
class MultiProviderScheduleSummary:
    status: str
    providers: tuple[ProviderScheduleReport, ...]
    retry_sent_count: int = 0
    retry_failed_count: int = 0
    retry_exhausted_count: int = 0
    response_saved: bool = False
    delivery_status: str = "NO_NEW_ITEMS"

    def safe_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "providers": [provider.safe_dict() for provider in self.providers],
            "retry_sent_count": self.retry_sent_count,
            "retry_failed_count": self.retry_failed_count,
            "retry_exhausted_count": self.retry_exhausted_count,
            "response_saved": False,
            "delivery_status": self.delivery_status,
        }


ProviderExecutor = Callable[[], Awaitable[ProviderCycleResult]]


class ProviderNotificationDispatcher(Protocol):
    available: bool

    async def deliver_new(
        self, provider: str, items: tuple[ProviderDisplayItem, ...]
    ) -> tuple[int, int, tuple[str, ...]]: ...

    async def deliver_retries(self, limit: int = 5) -> tuple[int, int, int]: ...


class ProviderTelegramFormatter:
    def format(self, items: tuple[ProviderDisplayItem, ...]) -> str:
        if not items:
            raise ValueError("telegram_feed_empty")
        blocks: list[str] = []
        for item in items:
            label = {
                "marketaux": "News",
                "finnhub": "Market data",
                "eia": "Official energy data",
                "sec_edgar": "Company filing",
            }.get(item.provider)
            if label is None:
                raise ValueError("telegram_provider_unsupported")
            lines = [f"{label}: {item.title}", f"Source: {item.source}"]
            lines.append(f"Time: {item.published_at.isoformat()}")
            if item.canonical_url:
                lines.append(item.canonical_url)
            blocks.append("\n".join(lines))
        message = "\n\n".join(blocks)
        if len(message) > 4096:
            raise ValueError("telegram_message_too_long")
        return message


class ReliableProviderNotificationService:
    """Reuse Notification's atomic claim/retry states for all approved providers."""

    available = True

    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        credential: TelegramRuntimeCredential,
        transport: TelegramTransport,
    ) -> None:
        self._factory = factory
        self._credential = credential
        self._transport = transport
        self._feed = ProviderFeedService(factory)
        self._formatter = ProviderTelegramFormatter()

    async def deliver_new(
        self, provider: str, items: tuple[ProviderDisplayItem, ...]
    ) -> tuple[int, int, tuple[str, ...]]:
        claimed, ids = await self._claim_new(provider, items)
        return await self._deliver(claimed, ids)

    async def deliver_retries(self, limit: int = 5) -> tuple[int, int, int]:
        items, ids, exhausted = await self._claim_retries(limit)
        sent, failed, _ = await self._deliver(items, ids)
        return sent, failed, exhausted

    async def _claim_new(
        self, provider: str, items: tuple[ProviderDisplayItem, ...]
    ) -> tuple[tuple[ProviderDisplayItem, ...], tuple[uuid.UUID, ...]]:
        claimed: list[ProviderDisplayItem] = []
        ids: list[uuid.UUID] = []
        now = datetime.now(UTC)
        async with self._factory() as session:
            for item in items:
                notification_id = await session.scalar(
                    insert(Notification)
                    .values(
                        content_item_id=item.content_item_id,
                        priority=NotificationPriority.P3,
                        priority_reason=f"new_{provider}_display_item",
                        policy_rule_id=POLICY_ID,
                        policy_version="1",
                        channel=NotificationChannel.TELEGRAM_PUSH,
                        dedup_key=f"{provider}:telegram:{item.content_item_id}",
                        payload_version=1,
                        status=NotificationStatus.SENDING,
                        scheduled_at=now,
                        retry_count=0,
                    )
                    .on_conflict_do_nothing(index_elements=["dedup_key"])
                    .returning(Notification.id)
                )
                if notification_id is not None:
                    claimed.append(item)
                    ids.append(notification_id)
            await session.commit()
        return tuple(claimed), tuple(ids)

    async def _claim_retries(
        self, limit: int
    ) -> tuple[tuple[ProviderDisplayItem, ...], tuple[uuid.UUID, ...], int]:
        now = datetime.now(UTC)
        stale = now - timedelta(seconds=SENDING_STALE_AFTER_SECONDS)
        ids: list[uuid.UUID] = []
        content_ids: list[uuid.UUID] = []
        async with self._factory() as session:
            candidates = tuple(
                await session.scalars(
                    select(Notification.id)
                    .where(
                        Notification.policy_rule_id == POLICY_ID,
                        Notification.retry_count < MAX_DELIVERY_ATTEMPTS,
                        or_(
                            Notification.status == NotificationStatus.PENDING,
                            Notification.status == NotificationStatus.FAILED,
                            (Notification.status == NotificationStatus.SENDING)
                            & (Notification.scheduled_at < stale),
                        ),
                    )
                    .order_by(Notification.scheduled_at, Notification.id)
                    .limit(limit)
                )
            )
            for notification_id in candidates:
                row = (
                    await session.execute(
                        update(Notification)
                        .where(
                            Notification.id == notification_id,
                            Notification.retry_count < MAX_DELIVERY_ATTEMPTS,
                            or_(
                                Notification.status == NotificationStatus.PENDING,
                                Notification.status == NotificationStatus.FAILED,
                                (Notification.status == NotificationStatus.SENDING)
                                & (Notification.scheduled_at < stale),
                            ),
                        )
                        .values(
                            status=NotificationStatus.SENDING,
                            retry_count=case(
                                (
                                    Notification.status == NotificationStatus.PENDING,
                                    Notification.retry_count,
                                ),
                                else_=Notification.retry_count + 1,
                            ),
                            scheduled_at=now,
                            failure_code=None,
                        )
                        .returning(Notification.id, Notification.content_item_id)
                    )
                ).one_or_none()
                if row is not None and row.content_item_id is not None:
                    ids.append(row.id)
                    content_ids.append(row.content_item_id)
            exhausted = int(
                (
                    await session.scalar(
                        select(func.count())
                        .select_from(Notification)
                        .where(
                            Notification.policy_rule_id == POLICY_ID,
                            Notification.retry_count >= MAX_DELIVERY_ATTEMPTS,
                            Notification.status.in_(
                                (NotificationStatus.FAILED, NotificationStatus.SENDING)
                            ),
                        )
                    )
                )
                or 0
            )
            await session.commit()
        items = await self._feed.by_content_ids(tuple(content_ids))
        available = {item.content_item_id for item in items}
        filtered = tuple(
            item_id
            for item_id, content_id in zip(ids, content_ids, strict=True)
            if content_id in available
        )
        return items, filtered, exhausted

    async def _deliver(
        self, items: tuple[ProviderDisplayItem, ...], ids: tuple[uuid.UUID, ...]
    ) -> tuple[int, int, tuple[str, ...]]:
        if not items:
            return 0, 0, ()
        try:
            message = self._formatter.format(items)
            response = await self._transport.send(self._credential, message)
            if not 200 <= response.status_code < 300:
                raise RuntimeError("telegram_request_rejected")
        except (RuntimeError, ValueError) as exc:
            code = (
                str(exc)
                if str(exc)
                in {
                    "telegram_request_rejected",
                    "telegram_transport_failed",
                    "telegram_message_too_long",
                    "telegram_provider_unsupported",
                }
                else "telegram_delivery_failed"
            )
            await self._mark(ids, NotificationStatus.FAILED, code)
            return 0, len(items), (code,)
        await self._mark(ids, NotificationStatus.SENT, None)
        return len(items), 0, ()

    async def _mark(
        self, ids: tuple[uuid.UUID, ...], status: NotificationStatus, failure: str | None
    ) -> None:
        if not ids:
            return
        async with self._factory() as session:
            await session.execute(
                update(Notification)
                .where(Notification.id.in_(ids), Notification.status == NotificationStatus.SENDING)
                .values(
                    status=status,
                    sent_at=datetime.now(UTC) if status is NotificationStatus.SENT else None,
                    failure_code=failure,
                )
            )
            await session.commit()


class PendingProviderNotificationService:
    """Persist new notification intent without claiming or consuming delivery state."""

    available = False

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory

    async def deliver_new(
        self, provider: str, items: tuple[ProviderDisplayItem, ...]
    ) -> tuple[int, int, tuple[str, ...]]:
        now = datetime.now(UTC)
        async with self._factory() as session:
            for item in items:
                await session.execute(
                    insert(Notification)
                    .values(
                        content_item_id=item.content_item_id,
                        priority=NotificationPriority.P3,
                        priority_reason=f"new_{provider}_display_item",
                        policy_rule_id=POLICY_ID,
                        policy_version="1",
                        channel=NotificationChannel.TELEGRAM_PUSH,
                        dedup_key=f"{provider}:telegram:{item.content_item_id}",
                        payload_version=1,
                        status=NotificationStatus.PENDING,
                        scheduled_at=now,
                        retry_count=0,
                    )
                    .on_conflict_do_nothing(index_elements=["dedup_key"])
                )
            await session.commit()
        return 0, 0, ("telegram_runtime_credential_missing",)

    async def deliver_retries(self, limit: int = 5) -> tuple[int, int, int]:
        del limit
        return 0, 0, 0


class MultiProviderTelegramScheduler:
    def __init__(
        self,
        executors: Mapping[str, ProviderExecutor],
        notifications: ProviderNotificationDispatcher,
    ) -> None:
        self._executors = dict(executors)
        self._notifications = notifications

    async def run(self) -> MultiProviderScheduleSummary:
        reports: list[ProviderScheduleReport] = []
        for provider in PROVIDER_ORDER:
            executor = self._executors.get(provider)
            if executor is None:
                result = ProviderCycleResult(
                    provider,
                    ProviderScheduleStatus.BLOCKED,
                    "not_started",
                    safe_errors=("provider_runtime_credential_missing",),
                )
            else:
                try:
                    result = await executor()
                except Exception:
                    result = ProviderCycleResult(
                        provider,
                        ProviderScheduleStatus.FAILED,
                        "failed",
                        safe_errors=("provider_cycle_failed",),
                    )
            sent = failed = 0
            errors = result.safe_errors
            delivery_errors: tuple[str, ...] = (
                () if self._notifications.available else ("telegram_runtime_credential_missing",)
            )
            delivery_status = "BLOCKED" if not self._notifications.available else "NO_NEW_ITEMS"
            if result.items:
                sent, failed, delivery_errors = await self._notifications.deliver_new(
                    provider, result.items
                )
                if not self._notifications.available:
                    delivery_status = "BLOCKED"
                elif failed:
                    delivery_status = "FAILED"
                elif sent:
                    delivery_status = "PASS"
            reports.append(
                ProviderScheduleReport(
                    provider,
                    result.status.value,
                    result.collection_status,
                    result.raw_item_count,
                    result.evidence_item_count,
                    result.content_item_count,
                    sent,
                    failed,
                    errors,
                    delivery_status,
                    delivery_errors,
                )
            )
        retry_sent, retry_failed, exhausted = await self._notifications.deliver_retries()
        delivery_status = (
            "BLOCKED"
            if not self._notifications.available
            else "FAILED"
            if retry_failed
            else "PASS"
            if retry_sent or any(report.delivery_status == "PASS" for report in reports)
            else "NO_NEW_ITEMS"
        )
        overall_status = (
            "PARTIAL"
            if any(
                report.status
                in {
                    ProviderScheduleStatus.FAILED.value,
                    ProviderScheduleStatus.BLOCKED.value,
                    ProviderScheduleStatus.RETRY.value,
                }
                for report in reports
            )
            or delivery_status in {"BLOCKED", "FAILED"}
            else "PASS"
        )
        return MultiProviderScheduleSummary(
            overall_status,
            tuple(reports),
            retry_sent,
            retry_failed,
            exhausted,
            delivery_status=delivery_status,
        )
