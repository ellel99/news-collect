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
from sqlalchemy import text, update
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
    ProviderCycleResult,
    ProviderScheduleStatus,
    ProviderTelegramFormatter,
    ReliableProviderNotificationService,
)
from market_intelligence.telegram.manual_push import (
    TelegramRuntimeCredential,
    TelegramSendResult,
)

POSTGRES_TEST_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://market_intelligence:local_dev_only@localhost:5432/market_intelligence",
)
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
