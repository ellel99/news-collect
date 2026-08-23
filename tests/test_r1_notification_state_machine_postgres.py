from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from market_intelligence.collection.control_plane_tools import ControlPlaneAuditService
from market_intelligence.collection.target_configs import build_operation_registry
from market_intelligence.db.base import system_metadata
from market_intelligence.db.models import (
    AuditLog,
    AuthorizationStatus,
    BodyAvailability,
    CollectionRun,
    CollectionRunStatus,
    ContentItem,
    ContentKind,
    DeletedStatus,
    IdentityStatus,
    Notification,
    NotificationChannel,
    NotificationPriority,
    NotificationStatus,
    ParseStatus,
    RawItem,
    Source,
    SourceAccount,
    SourceType,
)
from market_intelligence.notifications.delivery import NotificationDeliveryService
from market_intelligence.notifications.intent import (
    POLICY_ID,
    WATERMARK_KEY,
    IntentWatermark,
    NotificationIntentReconciler,
    create_pending_intent,
    record_intent_recovery,
)
from market_intelligence.telegram.manual_push import (
    TelegramRuntimeCredential,
    TelegramSendResult,
)

POSTGRES_TEST_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://market_intelligence:local_dev_only@localhost:5432/market_intelligence",
)


class _TelegramTransport:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code
        self.calls = 0

    async def send(self, credential: TelegramRuntimeCredential, message: str) -> TelegramSendResult:
        del credential
        assert "raw response" not in message.lower()
        self.calls += 1
        return TelegramSendResult(self.status_code)


async def _seed_watermark(session: AsyncSession, watermark: IntentWatermark) -> None:
    await session.execute(
        insert(system_metadata)
        .values(key=WATERMARK_KEY, value=watermark.encode())
        .on_conflict_do_nothing(index_elements=[system_metadata.c.key])
    )


@pytest.mark.asyncio
async def test_finnhub_quote_content_is_not_a_notification_candidate() -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    marker = uuid.uuid4().hex
    source_id, account_id, run_id, raw_id, content_id = await _content_fixture(factory, marker)
    try:
        async with factory.begin() as session:
            source = await session.get(Source, source_id)
            content = await session.get(ContentItem, content_id)
            assert source is not None and content is not None
            source.access_method = "finnhub"
            content.content_kind = ContentKind.FEED_ENTRY
            await _seed_watermark(
                session, IntentWatermark(datetime(2000, 1, 1, tzinfo=UTC), uuid.UUID(int=0))
            )
            assert await create_pending_intent(session, content_id) is None
    finally:
        await _cleanup(factory, source_id, account_id, run_id, raw_id, content_id)
        await engine.dispose()


@pytest.mark.asyncio
async def test_cutover_watermark_is_guarded_idempotent_and_immutable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    marker = uuid.uuid4().hex
    source_id, account_id, run_id, raw_id, content_id = await _content_fixture(factory, marker)
    try:
        service = ControlPlaneAuditService(factory, build_operation_registry())

        async def passed_audit() -> object:
            return type(
                "PassedAudit",
                (),
                {"running_runs": 0, "safe_errors": (), "eligible_count": 1},
            )()

        monkeypatch.setattr(service, "shadow", passed_audit)
        assert await service.prepare_cutover_watermark() is True
        assert await service.prepare_cutover_watermark() is False
    finally:
        await _cleanup(factory, source_id, account_id, run_id, raw_id, content_id)
        await engine.dispose()


async def _content_fixture(factory: async_sessionmaker, marker: str):  # type: ignore[no-untyped-def]
    async with factory.begin() as session:
        source = Source(
            code=f"r1-notification-{marker}",
            name="R1 Notification",
            source_type=SourceType.API,
            access_method="marketaux",
            authorization_status=AuthorizationStatus.AUTHORIZED,
            retention_class="metadata_only",
            enabled=True,
        )
        session.add(source)
        await session.flush()
        account = SourceAccount(
            source_id=source.id,
            identity_status=IdentityStatus.VERIFIED,
            enabled=True,
            collection_options={"query": "technology"},
        )
        session.add(account)
        await session.flush()
        run = CollectionRun(
            source_id=source.id,
            source_account_id=account.id,
            started_at=datetime.now(UTC),
            status=CollectionRunStatus.SUCCEEDED,
        )
        session.add(run)
        await session.flush()
        raw = RawItem(
            source_id=source.id,
            source_account_id=account.id,
            collection_run_id=run.id,
            external_id=f"notification-{marker}",
            fetched_at=datetime.now(UTC),
            http_status=200,
            content_type="application/json",
            payload_location=f"internal://notification/{marker}",
            payload_hash=marker.ljust(64, "0")[:64],
            retention_class="metadata_only",
            parse_status=ParseStatus.PENDING,
        )
        session.add(raw)
        await session.flush()
        content = ContentItem(
            raw_item_id=raw.id,
            source_id=source.id,
            source_account_id=account.id,
            content_kind=ContentKind.FEED_ENTRY,
            external_id=f"content-{marker}",
            title="Safe notification title",
            source_summary=None,
            body=None,
            body_availability=BodyAvailability.UNAVAILABLE,
            author=None,
            language=None,
            original_url=None,
            canonical_url=None,
            source_published_at=datetime.now(UTC),
            source_updated_at=None,
            first_seen_at=datetime.now(UTC),
            content_hash=None,
            reply_to_external_id=None,
            quote_external_id=None,
            repost_external_id=None,
            deleted_status=DeletedStatus.UNKNOWN,
            metadata_={"provider": "marketaux"},
        )
        session.add(content)
        await session.flush()
        return source.id, account.id, run.id, raw.id, content.id


@pytest.mark.asyncio
async def test_recovery_is_prioritized_exactly_resolved_and_bounded() -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    marker = uuid.uuid4().hex
    source_id, account_id, run_id, raw_id, content_id = await _content_fixture(factory, marker)
    try:
        async with factory.begin() as session:
            await _seed_watermark(
                session,
                IntentWatermark(datetime(2000, 1, 1, tzinfo=UTC), uuid.UUID(int=0)),
            )
            await record_intent_recovery(session, content_id)
            await record_intent_recovery(session, content_id)
        report = await NotificationIntentReconciler(factory).reconcile(limit=2)
        assert report.scanned == 2
        assert report.resolved == 2
        async with factory() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(Notification)
                    .where(Notification.content_item_id == content_id)
                )
                == 1
            )
            recoveries = tuple(
                await session.scalars(
                    select(AuditLog).where(
                        AuditLog.action == "notification_intent_recovery",
                        AuditLog.target_id == content_id,
                    )
                )
            )
            resolutions = tuple(
                await session.scalars(
                    select(AuditLog).where(
                        AuditLog.action == "notification_intent_recovery_resolved",
                        AuditLog.target_id == content_id,
                    )
                )
            )
            assert {item.actor_id for item in resolutions} == {str(item.id) for item in recoveries}
            assert (
                "safe notification title"
                not in repr([(item.before, item.after) for item in resolutions]).lower()
            )
        assert (await NotificationIntentReconciler(factory).reconcile(limit=2)).scanned == 0
    finally:
        await _cleanup(factory, source_id, account_id, run_id, raw_id, content_id)
        await engine.dispose()


@pytest.mark.asyncio
async def test_delivery_credential_missing_preserves_state_and_policy_isolated() -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    marker = uuid.uuid4().hex
    source_id, account_id, run_id, raw_id, content_id = await _content_fixture(factory, marker)
    transport = _TelegramTransport()
    try:
        async with factory.begin() as session:
            eligible = Notification(
                content_item_id=content_id,
                priority=NotificationPriority.P3,
                priority_reason="test",
                policy_rule_id=POLICY_ID,
                policy_version="1",
                channel=NotificationChannel.TELEGRAM_PUSH,
                dedup_key=f"marketaux:telegram:{content_id}",
                payload_version=1,
                status=NotificationStatus.PENDING,
                scheduled_at=datetime.now(UTC),
                retry_count=0,
            )
            isolated = Notification(
                content_item_id=content_id,
                priority=NotificationPriority.P3,
                priority_reason="test",
                policy_rule_id="other-policy",
                policy_version="1",
                channel=NotificationChannel.TELEGRAM_PUSH,
                dedup_key=f"other:telegram:{content_id}",
                payload_version=1,
                status=NotificationStatus.PENDING,
                scheduled_at=datetime.now(UTC),
                retry_count=0,
            )
            session.add_all((eligible, isolated))
            await session.flush()
            eligible_id, isolated_id = eligible.id, isolated.id

        service = NotificationDeliveryService(factory, transport)
        blocked = await service.deliver(None)
        assert blocked.status == "BLOCKED"
        assert transport.calls == 0
        async with factory() as session:
            assert (
                await session.get(Notification, eligible_id)
            ).status is NotificationStatus.PENDING
        passed = await service.deliver(TelegramRuntimeCredential("opaque", "opaque"), limit=5)
        assert (passed.status, passed.sent, passed.failed) == ("PASS", 1, 0)
        async with factory() as session:
            assert (await session.get(Notification, eligible_id)).status is NotificationStatus.SENT
            assert (
                await session.get(Notification, isolated_id)
            ).status is NotificationStatus.PENDING
    finally:
        await _cleanup(factory, source_id, account_id, run_id, raw_id, content_id)
        await engine.dispose()


async def _cleanup(
    factory: async_sessionmaker,
    source_id: uuid.UUID,
    account_id: uuid.UUID,
    run_id: uuid.UUID,
    raw_id: uuid.UUID,
    content_id: uuid.UUID,
) -> None:
    async with factory.begin() as session:
        await session.execute(delete(AuditLog).where(AuditLog.action.like("r1_phase2%")))
        await session.execute(delete(AuditLog).where(AuditLog.target_id == content_id))
        await session.execute(
            delete(Notification).where(Notification.content_item_id == content_id)
        )
        await session.execute(delete(ContentItem).where(ContentItem.id == content_id))
        await session.execute(delete(RawItem).where(RawItem.id == raw_id))
        await session.execute(delete(CollectionRun).where(CollectionRun.id == run_id))
        await session.execute(delete(SourceAccount).where(SourceAccount.id == account_id))
        await session.execute(delete(Source).where(Source.id == source_id))
        await session.execute(delete(system_metadata).where(system_metadata.c.key == WATERMARK_KEY))
