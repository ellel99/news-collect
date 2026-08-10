"""Minimal Marketaux collection and Telegram delivery cycle."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from market_intelligence.collection.contracts import CollectionTarget
from market_intelligence.db.models import (
    Notification,
    NotificationChannel,
    NotificationPriority,
    NotificationStatus,
)
from market_intelligence.evidence.end_to_end import EndToEndStatus
from market_intelligence.feed.marketaux_feed import MarketauxFeedService, VisibleFeedItem
from market_intelligence.pipeline.marketaux_real_collection import MarketauxRealCollectionPipeline
from market_intelligence.telegram.manual_push import (
    ManualTelegramPushService,
    TelegramRuntimeCredential,
    TelegramTransport,
)

_POLICY_ID = "spec-0035-marketaux-telegram"
_POLICY_VERSION = "1"
MAX_DELIVERY_ATTEMPTS = 3
SENDING_STALE_AFTER_SECONDS = 300


@dataclass(frozen=True, slots=True)
class SchedulerRunSummary:
    provider: str
    status: str
    collection_status: str
    raw_item_count: int
    evidence_item_count: int
    content_item_count: int
    new_notification_count: int
    retry_notification_count: int
    retry_exhausted_count: int
    sent_count: int
    failed_count: int
    response_saved: bool
    marketaux_token_read: bool
    telegram_credential_read: bool
    safe_errors: tuple[str, ...] = ()

    def safe_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["safe_errors"] = list(self.safe_errors)
        return value


class MarketauxTelegramScheduler:
    """Run one explicit cycle with injected runtime dependencies."""

    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        redis: Redis,
        collection: MarketauxRealCollectionPipeline,
        telegram_credential: TelegramRuntimeCredential,
        telegram_transport: TelegramTransport,
    ) -> None:
        self._factory = factory
        self._redis = redis
        self._collection = collection
        self._feed = MarketauxFeedService(factory)
        self._telegram_credential = telegram_credential
        self._telegram_transport = telegram_transport
        self._push = ManualTelegramPushService()

    async def run(self, target: CollectionTarget, *, limit: int) -> SchedulerRunSummary:
        if not 1 <= limit <= 3:
            return _summary("BLOCKED", "not_started", errors=("scheduler_limit_invalid",))
        outcome = await self._collection.run(target)
        evidence_count = sum(
            1
            for trigger in outcome.trigger_outcomes
            if trigger.pipeline_outcome is not None
            and trigger.pipeline_outcome.evidence_item_id is not None
        )
        collection_errors = tuple(error.code for error in outcome.safe_errors)
        items: tuple[VisibleFeedItem, ...] = ()
        if outcome.status is EndToEndStatus.PROCESSED and outcome.collection_run_id is not None:
            items = await self._feed.for_run(outcome.collection_run_id, limit)

        new_items, new_ids = await self._claim_new(items)
        retry_items, retry_ids, exhausted = await self._claim_retries(limit)
        claimed = new_items + retry_items
        notification_ids = new_ids + retry_ids
        if not claimed:
            status = "NO_NEW_ITEMS" if outcome.status is EndToEndStatus.PROCESSED else "FAIL"
            errors = collection_errors
            if status == "FAIL" and not errors:
                errors = ("collection_not_succeeded",)
            return _summary(
                status,
                outcome.status.value,
                raw=outcome.raw_item_count,
                evidence=evidence_count,
                content=len(items),
                exhausted=exhausted,
                errors=errors,
            )
        try:
            result = await self._push.push(
                claimed, self._telegram_credential, self._telegram_transport
            )
        except ValueError:
            await self._mark(
                notification_ids, NotificationStatus.FAILED, "telegram_message_invalid"
            )
            return _summary(
                "FAIL",
                outcome.status.value,
                raw=outcome.raw_item_count,
                evidence=evidence_count,
                content=len(items),
                new=len(new_items),
                retry=len(retry_items),
                exhausted=exhausted,
                failed=len(claimed),
                errors=(*collection_errors, "telegram_message_invalid"),
            )
        except RuntimeError:
            await self._mark(
                notification_ids, NotificationStatus.FAILED, "telegram_transport_failed"
            )
            return _summary(
                "FAIL",
                outcome.status.value,
                raw=outcome.raw_item_count,
                evidence=evidence_count,
                content=len(items),
                new=len(new_items),
                retry=len(retry_items),
                exhausted=exhausted,
                failed=len(claimed),
                errors=(*collection_errors, "telegram_transport_failed"),
            )
        if not 200 <= result.status_code < 300:
            await self._mark(
                notification_ids, NotificationStatus.FAILED, "telegram_request_rejected"
            )
            return _summary(
                "FAIL",
                outcome.status.value,
                raw=outcome.raw_item_count,
                evidence=evidence_count,
                content=len(items),
                new=len(new_items),
                retry=len(retry_items),
                exhausted=exhausted,
                failed=len(claimed),
                errors=(*collection_errors, "telegram_request_rejected"),
            )
        await self._mark(notification_ids, NotificationStatus.SENT, None)
        return _summary(
            "PASS",
            outcome.status.value,
            raw=outcome.raw_item_count,
            evidence=evidence_count,
            content=len(items),
            new=len(new_items),
            retry=len(retry_items),
            exhausted=exhausted,
            sent=len(claimed),
            errors=collection_errors,
        )

    async def _claim_new(
        self, items: tuple[VisibleFeedItem, ...]
    ) -> tuple[tuple[VisibleFeedItem, ...], tuple[uuid.UUID, ...]]:
        claimed: list[VisibleFeedItem] = []
        notification_ids: list[uuid.UUID] = []
        now = datetime.now(UTC)
        async with self._factory() as session:
            for item in items:
                notification_id = await session.scalar(
                    insert(Notification)
                    .values(
                        content_item_id=item.content_item_id,
                        priority=NotificationPriority.P3,
                        priority_reason="new_marketaux_visible_item",
                        policy_rule_id=_POLICY_ID,
                        policy_version=_POLICY_VERSION,
                        channel=NotificationChannel.TELEGRAM_PUSH,
                        dedup_key=f"marketaux:telegram:{item.content_item_id}",
                        payload_version=1,
                        status=NotificationStatus.SENDING,
                        scheduled_at=now,
                        sent_at=None,
                        failure_code=None,
                        retry_count=0,
                    )
                    .on_conflict_do_nothing(index_elements=["dedup_key"])
                    .returning(Notification.id)
                )
                if notification_id is not None:
                    claimed.append(item)
                    notification_ids.append(notification_id)
            await session.commit()
        return tuple(claimed), tuple(notification_ids)

    async def _claim_retries(
        self, limit: int
    ) -> tuple[tuple[VisibleFeedItem, ...], tuple[uuid.UUID, ...], int]:
        """Atomically reclaim bounded FAILED and stale SENDING notifications."""

        now = datetime.now(UTC)
        stale_before = now - timedelta(seconds=SENDING_STALE_AFTER_SECONDS)
        claimed_ids: list[uuid.UUID] = []
        content_ids: list[uuid.UUID] = []
        async with self._factory() as session:
            candidates = tuple(
                await session.scalars(
                    select(Notification.id)
                    .where(
                        Notification.policy_rule_id == _POLICY_ID,
                        Notification.channel == NotificationChannel.TELEGRAM_PUSH,
                        Notification.retry_count < MAX_DELIVERY_ATTEMPTS,
                        or_(
                            Notification.status == NotificationStatus.FAILED,
                            (
                                (Notification.status == NotificationStatus.SENDING)
                                & (Notification.scheduled_at < stale_before)
                            ),
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
                                Notification.status == NotificationStatus.FAILED,
                                (
                                    (Notification.status == NotificationStatus.SENDING)
                                    & (Notification.scheduled_at < stale_before)
                                ),
                            ),
                        )
                        .values(
                            status=NotificationStatus.SENDING,
                            retry_count=Notification.retry_count + 1,
                            scheduled_at=now,
                            sent_at=None,
                            failure_code=None,
                        )
                        .returning(Notification.id, Notification.content_item_id)
                    )
                ).one_or_none()
                if row is not None and row.content_item_id is not None:
                    claimed_ids.append(row.id)
                    content_ids.append(row.content_item_id)
            exhausted = int(
                (
                    await session.scalar(
                        select(func.count())
                        .select_from(Notification)
                        .where(
                            Notification.policy_rule_id == _POLICY_ID,
                            Notification.channel == NotificationChannel.TELEGRAM_PUSH,
                            Notification.retry_count >= MAX_DELIVERY_ATTEMPTS,
                            or_(
                                Notification.status == NotificationStatus.FAILED,
                                (
                                    (Notification.status == NotificationStatus.SENDING)
                                    & (Notification.scheduled_at < stale_before)
                                ),
                            ),
                        )
                    )
                )
                or 0
            )
            await session.commit()
        items = await self._feed.by_content_ids(tuple(content_ids))
        item_ids = {item.content_item_id for item in items}
        filtered_ids = tuple(
            notification_id
            for notification_id, content_id in zip(claimed_ids, content_ids, strict=True)
            if content_id in item_ids
        )
        return items, filtered_ids, exhausted

    async def _mark(
        self,
        notification_ids: tuple[uuid.UUID, ...],
        status: NotificationStatus,
        failure_code: str | None,
    ) -> None:
        if not notification_ids:
            return
        async with self._factory() as session:
            await session.execute(
                update(Notification)
                .where(
                    Notification.id.in_(notification_ids),
                    Notification.status == NotificationStatus.SENDING,
                )
                .values(
                    status=status,
                    sent_at=datetime.now(UTC) if status is NotificationStatus.SENT else None,
                    failure_code=failure_code,
                )
            )
            await session.commit()


def _summary(
    status: str,
    collection_status: str,
    *,
    raw: int = 0,
    evidence: int = 0,
    content: int = 0,
    new: int = 0,
    retry: int = 0,
    exhausted: int = 0,
    sent: int = 0,
    failed: int = 0,
    errors: tuple[str, ...] = (),
) -> SchedulerRunSummary:
    return SchedulerRunSummary(
        provider="marketaux",
        status=status,
        collection_status=collection_status,
        raw_item_count=raw,
        evidence_item_count=evidence,
        content_item_count=content,
        new_notification_count=new,
        retry_notification_count=retry,
        retry_exhausted_count=exhausted,
        sent_count=sent,
        failed_count=failed,
        response_saved=False,
        marketaux_token_read=True,
        telegram_credential_read=True,
        safe_errors=errors,
    )
