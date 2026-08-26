"""Deterministic Notification intent creation and bounded recovery."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import String, and_, cast, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from market_intelligence.db.base import system_metadata
from market_intelligence.db.models import (
    AuditLog,
    AuthorizationStatus,
    ContentItem,
    ContentKind,
    Notification,
    NotificationChannel,
    NotificationPriority,
    NotificationStatus,
    Source,
)

POLICY_ID = "spec-0038-multi-provider-telegram"
POLICY_VERSION = "1"
SCAN_ACTION = f"notification_intent_candidate_scanned_v{POLICY_VERSION}"
WATERMARK_KEY = "notification.intent.cutover.v1"
_PROVIDERS = frozenset({"marketaux", "finnhub", "eia", "sec_edgar"})
_POLICY_KINDS = {
    "finnhub": frozenset({ContentKind.ARTICLE}),
    "marketaux": frozenset({ContentKind.ARTICLE, ContentKind.FEED_ENTRY}),
    "sec_edgar": frozenset({ContentKind.OFFICIAL_RELEASE}),
}
_SECRET = re.compile(r"(?i)(api[_-]?key|api[_-]?token|authorization|token|secret|password)")


@dataclass(frozen=True, order=True, slots=True)
class IntentWatermark:
    created_at: datetime
    content_item_id: UUID

    def encode(self) -> str:
        return f"{self.created_at.astimezone(UTC).isoformat()}|{self.content_item_id}"

    @classmethod
    def decode(cls, value: str) -> IntentWatermark:
        try:
            timestamp, identity = value.split("|", 1)
            result = cls(datetime.fromisoformat(timestamp), UUID(identity))
        except (TypeError, ValueError):
            raise ValueError("notification_cutover_watermark_invalid") from None
        if result.created_at.tzinfo is None:
            raise ValueError("notification_cutover_watermark_invalid")
        return result


def notification_dedup_key(provider: str, content_item_id: UUID) -> str:
    if provider not in _PROVIDERS:
        raise ValueError("notification_provider_invalid")
    return f"{provider}:telegram:{content_item_id}"


async def _persist_cutover_watermark(session: AsyncSession, watermark: IntentWatermark) -> bool:
    """Private primitive; runtime callers must use the guarded control-plane operation."""
    created = await session.scalar(
        insert(system_metadata)
        .values(key=WATERMARK_KEY, value=watermark.encode())
        .on_conflict_do_nothing(index_elements=[system_metadata.c.key])
        .returning(system_metadata.c.key)
    )
    return created is not None


async def load_cutover_watermark(session: AsyncSession) -> IntentWatermark | None:
    value = await session.scalar(
        select(system_metadata.c.value).where(system_metadata.c.key == WATERMARK_KEY)
    )
    return None if value is None else IntentWatermark.decode(value)


async def create_pending_intent(
    session: AsyncSession, content_item_id: UUID, *, now: datetime | None = None
) -> UUID | None:
    candidate = await _candidate(session, content_item_id)
    if candidate is None:
        return None
    _, provider = candidate
    current = now or datetime.now(UTC)
    return await session.scalar(
        insert(Notification)
        .values(
            content_item_id=content_item_id,
            priority=NotificationPriority.P3,
            priority_reason=f"new_{provider}_display_item",
            policy_rule_id=POLICY_ID,
            policy_version=POLICY_VERSION,
            channel=NotificationChannel.TELEGRAM_PUSH,
            dedup_key=notification_dedup_key(provider, content_item_id),
            payload_version=1,
            status=NotificationStatus.PENDING,
            scheduled_at=current,
            retry_count=0,
        )
        .on_conflict_do_nothing(index_elements=[Notification.dedup_key])
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
            after={
                "policy_id": POLICY_ID,
                "policy_version": POLICY_VERSION,
                "status": "pending",
                "safe_error": "notification_intent_pending_recovery",
            },
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

    async def reconcile(
        self, watermark: datetime | None = None, *, limit: int = 100
    ) -> ReconcileReport:
        del watermark  # legacy argument cannot override persisted cutover authority
        if not 1 <= limit <= 500:
            raise ValueError("notification_reconcile_limit_invalid")
        created = resolved = scanned = 0
        async with self._factory.begin() as session:
            cutover = await load_cutover_watermark(session)
            if cutover is None:
                return ReconcileReport(0, 0, 0)
            resolution = aliased(AuditLog)
            recoveries = tuple(
                await session.scalars(
                    select(AuditLog)
                    .where(
                        AuditLog.action == "notification_intent_recovery",
                        ~select(resolution.id)
                        .where(
                            resolution.action == "notification_intent_recovery_resolved",
                            resolution.actor_id == cast(AuditLog.id, String),
                        )
                        .exists(),
                    )
                    .order_by(AuditLog.created_at, AuditLog.id)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            )
            for recovery in recoveries:
                scanned += 1
                if recovery.target_id is None:
                    _resolve_recovery(session, recovery, "blocked")
                    resolved += 1
                    continue
                candidate = await _candidate(session, recovery.target_id)
                if candidate is None:
                    _resolve_recovery(session, recovery, "blocked")
                    resolved += 1
                    continue
                await create_pending_intent(session, recovery.target_id)
                _resolve_recovery(session, recovery, "resolved")
                resolved += 1
            remaining = limit - scanned
            if remaining:
                scanned_candidate = aliased(AuditLog)
                ids = tuple(
                    await session.scalars(
                        select(ContentItem.id)
                        .join(Source, Source.id == ContentItem.source_id)
                        .where(
                            or_(
                                ContentItem.created_at > cutover.created_at,
                                (ContentItem.created_at == cutover.created_at)
                                & (ContentItem.id > cutover.content_item_id),
                            ),
                            ~ContentItem.notifications.any(
                                and_(
                                    Notification.policy_rule_id == POLICY_ID,
                                    Notification.channel == NotificationChannel.TELEGRAM_PUSH,
                                )
                            ),
                            ~select(scanned_candidate.id)
                            .where(
                                scanned_candidate.action == SCAN_ACTION,
                                scanned_candidate.target_id == ContentItem.id,
                            )
                            .exists(),
                            Source.enabled.is_(True),
                            Source.authorization_status.in_(
                                (
                                    AuthorizationStatus.AUTHORIZED,
                                    AuthorizationStatus.IMPLEMENTED,
                                )
                            ),
                            or_(
                                and_(
                                    Source.access_method == "finnhub",
                                    ContentItem.content_kind == ContentKind.ARTICLE,
                                    ContentItem.metadata_["operation_key"].astext == "company_news",
                                ),
                                and_(
                                    Source.access_method == "marketaux",
                                    ContentItem.content_kind.in_(
                                        (
                                            ContentKind.ARTICLE,
                                            ContentKind.FEED_ENTRY,
                                        )
                                    ),
                                ),
                                and_(
                                    Source.access_method == "sec_edgar",
                                    ContentItem.content_kind == ContentKind.OFFICIAL_RELEASE,
                                ),
                            ),
                        )
                        .order_by(ContentItem.created_at, ContentItem.id)
                        .limit(remaining)
                        .with_for_update(skip_locked=True, of=ContentItem)
                    )
                )
                for content_id in ids:
                    scanned += 1
                    if await create_pending_intent(session, content_id) is not None:
                        created += 1
                    else:
                        _record_scanned_candidate(session, content_id)
            return ReconcileReport(scanned, created, resolved)


async def _candidate(
    session: AsyncSession, content_item_id: UUID
) -> tuple[ContentItem, str] | None:
    row = (
        await session.execute(
            select(ContentItem, Source)
            .join(Source, Source.id == ContentItem.source_id)
            .where(ContentItem.id == content_item_id)
        )
    ).one_or_none()
    if row is None:
        return None
    content, source = row
    watermark = await load_cutover_watermark(session)
    if watermark is None or (content.created_at, content.id) <= (
        watermark.created_at,
        watermark.content_item_id,
    ):
        return None
    provider = source.access_method
    if (
        provider not in _PROVIDERS
        or not source.enabled
        or source.authorization_status
        not in {AuthorizationStatus.AUTHORIZED, AuthorizationStatus.IMPLEMENTED}
        or content.content_kind not in _POLICY_KINDS.get(provider, frozenset())
        or (provider == "finnhub" and content.metadata_.get("operation_key") != "company_news")
        or content.source_published_at is None
        or not _safe_text(content.title)
        or not _safe_url(content.canonical_url)
    ):
        return None
    return content, provider


def _safe_text(value: str | None) -> bool:
    return bool(value and value.strip() and not _SECRET.search(value))


def _safe_url(value: str | None) -> bool:
    if value is None:
        return True
    if _SECRET.search(value):
        return False
    parsed = urlsplit(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname) and parsed.username is None


def _resolve_recovery(session: AsyncSession, recovery: AuditLog, status: str) -> None:
    after = {
        "policy_id": POLICY_ID,
        "policy_version": POLICY_VERSION,
        "status": status,
    }
    if status == "blocked":
        after["safe_error"] = "notification_candidate_invalid"
    session.add(
        AuditLog(
            actor_type="system",
            actor_id=str(recovery.id),
            action="notification_intent_recovery_resolved",
            target_type="content_item",
            target_id=recovery.target_id,
            before=None,
            after=after,
        )
    )


def _record_scanned_candidate(session: AsyncSession, content_item_id: UUID) -> None:
    """Durably advance bounded scans past a value-free invalid candidate."""
    session.add(
        AuditLog(
            actor_type="system",
            actor_id=None,
            action=SCAN_ACTION,
            target_type="content_item",
            target_id=content_item_id,
            before=None,
            after={
                "policy_id": POLICY_ID,
                "policy_version": POLICY_VERSION,
                "status": "not_applicable",
                "safe_error": "notification_candidate_invalid",
            },
        )
    )
