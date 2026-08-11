import asyncio
import inspect
import json
import os
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from redis.asyncio import Redis
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from market_intelligence.db import Base
from market_intelligence.db.models import (
    AuthorizationStatus,
    BodyAvailability,
    CollectionRun,
    CollectionRunStatus,
    ContentItem,
    ContentKind,
    DeletedStatus,
    Notification,
    NotificationChannel,
    NotificationPriority,
    NotificationStatus,
    ParseStatus,
    RawItem,
    Source,
    SourceType,
)
from market_intelligence.feed.provider_feed import ProviderDisplayItem
from market_intelligence.scheduler.marketaux_telegram import (
    MAX_DELIVERY_ATTEMPTS,
    SENDING_STALE_AFTER_SECONDS,
)
from market_intelligence.scheduler.multi_provider import (
    PROVIDER_ORDER,
    MultiProviderTelegramScheduler,
    PendingProviderNotificationService,
    ProviderCycleResult,
    ProviderScheduleStatus,
    ProviderTelegramFormatter,
    ReliableProviderNotificationService,
)
from market_intelligence.scheduler.multi_provider_runtime import (
    ProviderCadenceController,
    collection_schedule_status,
)
from market_intelligence.telegram.manual_push import (
    TelegramRuntimeCredential,
    TelegramSendResult,
)

POSTGRES_TEST_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://market_intelligence:local_dev_only@localhost:5432/market_intelligence",
)
REDIS_TEST_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/13")
SECRET = "scheduler-secret-never-render"


class MockTelegram:
    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.messages: list[str] = []

    async def send(self, credential, message: str) -> TelegramSendResult:
        assert credential.token == SECRET
        self.messages.append(message)
        return TelegramSendResult(self.status)


@pytest_asyncio.fixture
async def scheduler_db() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    schema = f"spec_0038_{uuid.uuid4().hex}"
    engine = create_async_engine(
        POSTGRES_TEST_URL, connect_args={"server_settings": {"search_path": schema}}
    )
    async with engine.connect() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        await connection.commit()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        async with engine.connect() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            await connection.commit()
        await engine.dispose()


async def _display(factory, provider: str) -> ProviderDisplayItem:
    now = datetime.now(UTC)
    async with factory.begin() as session:
        source = Source(
            code=f"{provider}-{uuid.uuid4().hex}",
            name=f"Synthetic {provider}",
            source_type=SourceType.API,
            access_method=provider,
            authorization_status=AuthorizationStatus.AUTHORIZED,
            retention_class="metadata_only",
            enabled=True,
        )
        session.add(source)
        await session.flush()
        run = CollectionRun(
            source_id=source.id,
            started_at=now,
            status=CollectionRunStatus.SUCCEEDED,
        )
        session.add(run)
        await session.flush()
        raw = RawItem(
            source_id=source.id,
            collection_run_id=run.id,
            external_id=f"{provider}-item",
            fetched_at=now,
            payload_location=f"internal://{provider}/item",
            payload_hash=uuid.uuid4().hex,
            retention_class="metadata_only",
            parse_status=ParseStatus.PARSED,
        )
        session.add(raw)
        await session.flush()
        content = ContentItem(
            raw_item_id=raw.id,
            source_id=source.id,
            content_kind=ContentKind.FEED_ENTRY,
            external_id=f"{provider}-item",
            title=f"Synthetic {provider} update",
            body_availability=BodyAvailability.UNAVAILABLE,
            first_seen_at=now,
            source_published_at=now,
            deleted_status=DeletedStatus.UNKNOWN,
            metadata_={"provider": provider},
        )
        session.add(content)
        await session.flush()
        return ProviderDisplayItem(content.id, provider, content.title, source.name, now, None)


class FakeNotifications:
    available = True

    def __init__(self) -> None:
        self.delivered: list[str] = []
        self.retry_calls = 0

    async def deliver_new(self, provider, items):
        self.delivered.append(provider)
        return len(items), 0, ()

    async def deliver_retries(self, limit=5):
        self.retry_calls += 1
        return 0, 0, 0


@pytest.mark.asyncio
async def test_four_provider_orchestration_isolates_failure() -> None:
    calls: list[str] = []
    notifications = FakeNotifications()

    def executor(provider: str, status: ProviderScheduleStatus):
        async def run():
            calls.append(provider)
            if provider == "finnhub":
                raise RuntimeError("synthetic")
            return ProviderCycleResult(provider, status, status.value.lower())

        return run

    scheduler = MultiProviderTelegramScheduler(
        {
            provider: executor(
                provider,
                ProviderScheduleStatus.NO_NEW_ITEMS
                if provider == "eia"
                else ProviderScheduleStatus.PASS,
            )
            for provider in PROVIDER_ORDER
        },
        notifications,  # type: ignore[arg-type]
    )

    summary = await scheduler.run()

    assert calls == list(PROVIDER_ORDER)
    assert [report.status for report in summary.providers] == [
        "PASS",
        "FAILED",
        "NO_NEW_ITEMS",
        "PASS",
    ]
    assert summary.status == "PARTIAL"
    assert notifications.retry_calls == 1


@pytest.mark.asyncio
async def test_missing_credential_blocks_only_one_provider() -> None:
    async def passed():
        return ProviderCycleResult("marketaux", ProviderScheduleStatus.PASS, "processed")

    summary = await MultiProviderTelegramScheduler(
        {"marketaux": passed},
        FakeNotifications(),  # type: ignore[arg-type]
    ).run()

    assert summary.providers[0].status == "PASS"
    assert all(report.status == "BLOCKED" for report in summary.providers[1:])
    assert summary.status == "PARTIAL"


@pytest.mark.asyncio
async def test_no_new_items_do_not_send_and_retries_still_run() -> None:
    notifications = FakeNotifications()

    def executor(provider: str):
        async def run():
            return ProviderCycleResult(
                provider,
                ProviderScheduleStatus.NO_NEW_ITEMS,
                "no_new_items",
            )

        return run

    summary = await MultiProviderTelegramScheduler(
        {provider: executor(provider) for provider in PROVIDER_ORDER},
        notifications,  # type: ignore[arg-type]
    ).run()

    assert summary.status == "PASS"
    assert notifications.delivered == []
    assert notifications.retry_calls == 1


@pytest.mark.asyncio
async def test_each_provider_routes_its_own_new_items() -> None:
    notifications = FakeNotifications()

    def executor(provider: str):
        async def run():
            item = ProviderDisplayItem(
                uuid.uuid4(),
                provider,
                f"Synthetic {provider}",
                "Synthetic source",
                datetime.now(UTC),
                None,
            )
            return ProviderCycleResult(
                provider,
                ProviderScheduleStatus.PASS,
                "processed",
                content_item_count=1,
                items=(item,),
            )

        return run

    summary = await MultiProviderTelegramScheduler(
        {provider: executor(provider) for provider in PROVIDER_ORDER},
        notifications,  # type: ignore[arg-type]
    ).run()

    assert summary.status == "PASS"
    assert notifications.delivered == list(PROVIDER_ORDER)


@pytest.mark.parametrize("provider", PROVIDER_ORDER)
def test_provider_specific_telegram_formatter(provider: str) -> None:
    item = ProviderDisplayItem(
        uuid.uuid4(), provider, f"Synthetic {provider}", "Synthetic source", datetime.now(UTC), None
    )
    message = ProviderTelegramFormatter().format((item,))
    expected = {
        "marketaux": "News",
        "finnhub": "Market data",
        "eia": "Official energy data",
        "sec_edgar": "Company filing",
    }[provider]
    assert message.startswith(expected)
    assert SECRET not in message


@pytest.mark.asyncio
async def test_sent_is_permanent_dedup(scheduler_db) -> None:
    item = await _display(scheduler_db, "sec_edgar")
    telegram = MockTelegram()
    service = ReliableProviderNotificationService(
        scheduler_db, TelegramRuntimeCredential(SECRET, "chat"), telegram
    )

    first = await service.deliver_new("sec_edgar", (item,))
    second = await service.deliver_new("sec_edgar", (item,))

    assert first == (1, 0, ())
    assert second == (0, 0, ())
    assert len(telegram.messages) == 1


@pytest.mark.asyncio
async def test_failed_retry_stale_recovery_and_exhaustion(scheduler_db) -> None:
    item = await _display(scheduler_db, "eia")
    failing = ReliableProviderNotificationService(
        scheduler_db, TelegramRuntimeCredential(SECRET, "chat"), MockTelegram(500)
    )
    await failing.deliver_new("eia", (item,))
    successful_transport = MockTelegram()
    successful = ReliableProviderNotificationService(
        scheduler_db, TelegramRuntimeCredential(SECRET, "chat"), successful_transport
    )

    sent, failed, exhausted = await successful.deliver_retries()

    assert (sent, failed, exhausted) == (1, 0, 0)
    async with scheduler_db.begin() as session:
        await session.execute(
            update(Notification).values(
                status=NotificationStatus.SENDING,
                sent_at=None,
                retry_count=0,
                scheduled_at=datetime.now(UTC) - timedelta(seconds=SENDING_STALE_AFTER_SECONDS + 1),
            )
        )
    sent, failed, exhausted = await successful.deliver_retries()
    assert (sent, failed, exhausted) == (1, 0, 0)
    async with scheduler_db.begin() as session:
        await session.execute(
            update(Notification).values(
                status=NotificationStatus.FAILED,
                retry_count=MAX_DELIVERY_ATTEMPTS,
            )
        )
    assert await successful.deliver_retries() == (0, 0, 1)


def test_scheduler_default_dry_run_is_inert() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/multi_provider_scheduler_smoke.py"],
        check=False,
        capture_output=True,
        text=True,
        env={"PATH": os.environ.get("PATH", "")},
    )
    report = json.loads(completed.stdout)
    assert completed.returncode == 0
    assert report["status"] == "DRY_RUN"
    assert all(item["status"] == "DRY_RUN" for item in report["providers"])
    assert report["delivery_status"] == "NO_NEW_ITEMS"


@pytest.mark.asyncio
async def test_telegram_missing_preserves_collection_and_pending_delivery(scheduler_db) -> None:
    items = {provider: await _display(scheduler_db, provider) for provider in PROVIDER_ORDER}
    calls: list[str] = []

    def executor(provider: str):
        async def run():
            calls.append(provider)
            return ProviderCycleResult(
                provider,
                ProviderScheduleStatus.PASS,
                "processed",
                raw_item_count=1,
                evidence_item_count=1,
                content_item_count=1,
                items=(items[provider],),
            )

        return run

    summary = await MultiProviderTelegramScheduler(
        {provider: executor(provider) for provider in PROVIDER_ORDER},
        PendingProviderNotificationService(scheduler_db),
    ).run()

    assert calls == list(PROVIDER_ORDER)
    assert all(report.status == "PASS" for report in summary.providers)
    assert all(report.collection_status == "processed" for report in summary.providers)
    assert all(report.delivery_status == "BLOCKED" for report in summary.providers)
    assert all(
        report.delivery_safe_errors == ("telegram_runtime_credential_missing",)
        for report in summary.providers
    )
    async with scheduler_db() as session:
        statuses = tuple(await session.scalars(select(Notification.status)))
    assert statuses == (NotificationStatus.PENDING,) * 4


@pytest.mark.asyncio
async def test_telegram_missing_does_not_consume_historical_notifications(scheduler_db) -> None:
    failed_item = await _display(scheduler_db, "finnhub")
    stale_item = await _display(scheduler_db, "eia")
    now = datetime.now(UTC)
    async with scheduler_db.begin() as session:
        session.add_all(
            [
                Notification(
                    content_item_id=failed_item.content_item_id,
                    priority=NotificationPriority.P3,
                    priority_reason="synthetic",
                    policy_rule_id="spec-0038-multi-provider-telegram",
                    policy_version="1",
                    channel=NotificationChannel.TELEGRAM_PUSH,
                    dedup_key=f"historical-failed-{uuid.uuid4()}",
                    payload_version=1,
                    status=NotificationStatus.FAILED,
                    scheduled_at=now,
                    retry_count=1,
                ),
                Notification(
                    content_item_id=stale_item.content_item_id,
                    priority=NotificationPriority.P3,
                    priority_reason="synthetic",
                    policy_rule_id="spec-0038-multi-provider-telegram",
                    policy_version="1",
                    channel=NotificationChannel.TELEGRAM_PUSH,
                    dedup_key=f"historical-sending-{uuid.uuid4()}",
                    payload_version=1,
                    status=NotificationStatus.SENDING,
                    scheduled_at=now - timedelta(seconds=SENDING_STALE_AFTER_SECONDS + 1),
                    retry_count=2,
                ),
            ]
        )

    service = PendingProviderNotificationService(scheduler_db)
    assert await service.deliver_retries() == (0, 0, 0)

    async with scheduler_db() as session:
        rows = tuple(
            (
                await session.execute(
                    select(Notification.status, Notification.retry_count).order_by(
                        Notification.retry_count
                    )
                )
            ).all()
        )
    assert rows == (
        (NotificationStatus.FAILED, 1),
        (NotificationStatus.SENDING, 2),
    )


@pytest.mark.asyncio
async def test_pending_delivery_sends_after_telegram_credential_recovers(scheduler_db) -> None:
    item = await _display(scheduler_db, "marketaux")
    pending = PendingProviderNotificationService(scheduler_db)
    await pending.deliver_new("marketaux", (item,))
    telegram = MockTelegram()
    recovered = ReliableProviderNotificationService(
        scheduler_db,
        TelegramRuntimeCredential(SECRET, "chat"),
        telegram,
    )

    assert await recovered.deliver_retries() == (1, 0, 0)
    async with scheduler_db() as session:
        status = await session.scalar(select(Notification.status))
    assert status is NotificationStatus.SENT
    assert len(telegram.messages) == 1


@pytest.mark.asyncio
async def test_provider_retry_gate_uses_short_delay_then_restores_cadence() -> None:
    redis = Redis.from_url(REDIS_TEST_URL, decode_responses=True)
    await redis.flushdb()
    controller = ProviderCadenceController(redis, max_retry_delay=10)
    run_id = uuid.uuid4()
    try:
        assert (await controller.claim("finnhub", 30)).status == "claimed"
        assert await controller.schedule_retry("finnhub", 1, run_id, 1) == 1
        assert (await controller.claim("finnhub", 30)).status == "retry_wait"
        await asyncio.sleep(1.1)
        retry_claim = await controller.claim("finnhub", 30)
        assert retry_claim.status == "claimed"
        assert retry_claim.collection_run_id == run_id
        assert retry_claim.attempt == 1
        assert (await controller.claim("finnhub", 30)).status == "not_due"
    finally:
        await redis.flushdb()
        await redis.aclose()


@pytest.mark.asyncio
async def test_non_retryable_failure_keeps_normal_cadence() -> None:
    redis = Redis.from_url(REDIS_TEST_URL, decode_responses=True)
    await redis.flushdb()
    controller = ProviderCadenceController(redis, max_retry_delay=10)
    try:
        assert (await controller.claim("sec_edgar", 30)).status == "claimed"
        assert (await controller.claim("sec_edgar", 30)).status == "not_due"
    finally:
        await redis.flushdb()
        await redis.aclose()


def test_retryable_collection_error_maps_to_retry_status() -> None:
    assert collection_schedule_status("COLLECTION_RATE_LIMITED") is ProviderScheduleStatus.RETRY
    assert collection_schedule_status("COLLECTION_TIMEOUT") is ProviderScheduleStatus.RETRY
    assert (
        collection_schedule_status("COLLECTION_CONTRACT_INVALID") is ProviderScheduleStatus.FAILED
    )


def test_scheduler_source_audit() -> None:
    import market_intelligence.scheduler.multi_provider as scheduler
    import market_intelligence.scheduler.multi_provider_runtime as runtime

    source = (inspect.getsource(scheduler) + inspect.getsource(runtime)).lower()
    forbidden = (
        "local_evaluation",
        "provider_capture",
        "openai",
        "recommendation",
        "clustering",
        "dotenv",
    )
    assert all(marker not in source for marker in forbidden)
