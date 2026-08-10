"""Minimal Marketaux collection and Telegram delivery cycle."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import update
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


@dataclass(frozen=True, slots=True)
class SchedulerRunSummary:
    provider: str
    status: str
    collection_status: str
    raw_item_count: int
    evidence_item_count: int
    content_item_count: int
    new_notification_count: int
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
        if outcome.status is not EndToEndStatus.PROCESSED or outcome.collection_run_id is None:
            errors = tuple(error.code for error in outcome.safe_errors) or (
                "collection_not_succeeded",
            )
            return _summary(
                "FAIL",
                outcome.status.value,
                raw=outcome.raw_item_count,
                evidence=evidence_count,
                errors=errors,
            )

        items = await self._feed.for_run(outcome.collection_run_id, limit)
        if not items:
            return _summary(
                "NO_NEW_ITEMS",
                outcome.status.value,
                raw=outcome.raw_item_count,
                evidence=evidence_count,
            )
        claimed, notification_ids = await self._claim(items)
        if not claimed:
            return _summary(
                "NO_NEW_ITEMS",
                outcome.status.value,
                raw=outcome.raw_item_count,
                evidence=evidence_count,
                content=len(items),
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
                new=len(claimed),
                failed=len(claimed),
                errors=("telegram_message_invalid",),
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
                new=len(claimed),
                failed=len(claimed),
                errors=("telegram_transport_failed",),
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
                new=len(claimed),
                failed=len(claimed),
                errors=("telegram_request_rejected",),
            )
        await self._mark(notification_ids, NotificationStatus.SENT, None)
        return _summary(
            "PASS",
            outcome.status.value,
            raw=outcome.raw_item_count,
            evidence=evidence_count,
            content=len(items),
            new=len(claimed),
            sent=len(claimed),
        )

    async def _claim(
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
        sent_count=sent,
        failed_count=failed,
        response_saved=False,
        marketaux_token_read=True,
        telegram_credential_read=True,
        safe_errors=errors,
    )
