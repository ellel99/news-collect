import asyncio
import inspect
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from redis.asyncio import Redis
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from market_intelligence.collection.contracts import CollectionTarget
from market_intelligence.core.config import Settings
from market_intelligence.db import Base, ContentItem, EvidenceItem, RawItem
from market_intelligence.db.models import (
    AuthorizationStatus,
    Notification,
    NotificationStatus,
    Source,
    SourceAccount,
    SourceType,
)
from market_intelligence.pipeline.marketaux_real_collection import MarketauxRealCollectionPipeline
from market_intelligence.providers.contracts import ProviderTransportResponse
from market_intelligence.providers.credentials import RuntimeCredential
from market_intelligence.providers.transport import MockProviderTransport
from market_intelligence.scheduler.marketaux_telegram import (
    MAX_DELIVERY_ATTEMPTS,
    SENDING_STALE_AFTER_SECONDS,
    MarketauxTelegramScheduler,
    _summary,
)
from market_intelligence.scheduler.runtime import run_scheduler_cycle
from market_intelligence.telegram.manual_push import (
    TelegramRuntimeCredential,
    TelegramSendResult,
)

POSTGRES_TEST_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://market_intelligence:local_dev_only@localhost:5432/market_intelligence",
)
REDIS_TEST_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/13")
SECRET = "synthetic-secret-never-output"


class MockTelegramTransport:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code
        self.messages: list[str] = []

    async def send(self, credential, message: str) -> TelegramSendResult:
        assert credential.token == SECRET
        self.messages.append(message)
        return TelegramSendResult(self.status_code)


class GuardedEnvironment(dict[str, str]):
    def get(self, key: str, default: str | None = None) -> str | None:
        raise AssertionError(f"credential read during dry-run: {key}")


@pytest_asyncio.fixture
async def scheduler_runtime() -> AsyncIterator[tuple[async_sessionmaker[AsyncSession], Redis]]:
    schema = f"spec_0035_scheduler_{uuid.uuid4().hex}"
    engine = create_async_engine(
        POSTGRES_TEST_URL,
        connect_args={"server_settings": {"search_path": schema}},
    )
    async with engine.connect() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        await connection.commit()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    redis = Redis.from_url(REDIS_TEST_URL, decode_responses=True)
    await redis.flushdb()
    try:
        yield async_sessionmaker(engine, expire_on_commit=False), redis
    finally:
        await redis.flushdb()
        await redis.aclose()
        async with engine.connect() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            await connection.commit()
        await engine.dispose()


async def _target(factory: async_sessionmaker[AsyncSession]) -> CollectionTarget:
    async with factory.begin() as session:
        source = Source(
            code=f"spec0035-{uuid.uuid4().hex}",
            name="Synthetic Marketaux Scheduler",
            source_type=SourceType.API,
            access_method="marketaux",
            authorization_status=AuthorizationStatus.AUTHORIZED,
            retention_class="metadata_only",
            enabled=True,
            schedule_seconds=900,
        )
        session.add(source)
        await session.flush()
        account = SourceAccount(
            source_id=source.id,
            identity_status="verified",
            enabled=True,
            collection_options={"query": "synthetic"},
        )
        session.add(account)
        await session.flush()
        return CollectionTarget(
            source.id,
            account.id,
            source.source_type.value,
            source.access_method,
            source.retention_class,
            account.collection_options,
        )


def _settings() -> Settings:
    return Settings(
        APP_ENV="test",
        COLLECTION_ADAPTER_TIMEOUT_SECONDS=2,
        COLLECTION_TASK_DEADLINE_SECONDS=5,
        COLLECTION_LOCK_TTL_SECONDS=4,
        COLLECTION_BATCH_LIMIT=1,
        _env_file=None,
    )


def _response(item_id: str, status_code: int = 200) -> ProviderTransportResponse:
    return ProviderTransportResponse(
        status_code=status_code,
        received_at=datetime.now(UTC),
        body={
            "data": [
                {
                    "uuid": item_id,
                    "published_at": "2026-08-10T10:00:00Z",
                    "title": "Synthetic scheduler headline",
                    "description": "not in summary",
                    "snippet": "not in summary",
                    "url": "https://example.invalid/synthetic-scheduler",
                }
            ]
        },
    )


def _scheduler(factory, redis, responses, telegram_status=200):
    collection = MarketauxRealCollectionPipeline(
        factory,
        redis,
        _settings(),
        RuntimeCredential("MARKETAUX_API_TOKEN", SECRET),
        MockProviderTransport(responses),
    )
    telegram = MockTelegramTransport(telegram_status)
    scheduler = MarketauxTelegramScheduler(
        factory,
        redis,
        collection,
        TelegramRuntimeCredential(SECRET, "synthetic-chat"),
        telegram,
    )
    return scheduler, telegram


@pytest.mark.asyncio
async def test_default_dry_run_reads_no_credentials_or_runtime() -> None:
    summary = await run_scheduler_cycle(execute=False, limit=1, environ=GuardedEnvironment())

    assert summary.status == "DRY_RUN"
    assert summary.marketaux_token_read is False
    assert summary.telegram_credential_read is False
    assert summary.sent_count == 0


@pytest.mark.asyncio
async def test_execute_missing_credentials_fails_before_runtime() -> None:
    summary = await run_scheduler_cycle(execute=True, limit=1, environ={})

    assert summary.status == "BLOCKED"
    assert summary.safe_errors == ("scheduler_runtime_credential_missing",)
    assert summary.marketaux_token_read is True
    assert summary.telegram_credential_read is True


@pytest.mark.asyncio
async def test_mock_cycle_writes_pipeline_and_sends_once(scheduler_runtime) -> None:
    factory, redis = scheduler_runtime
    target = await _target(factory)
    scheduler, telegram = _scheduler(factory, redis, [_response("scheduler-item-1")])

    summary = await scheduler.run(target, limit=1)

    assert summary.status == "PASS"
    assert (summary.raw_item_count, summary.evidence_item_count) == (1, 1)
    assert (summary.content_item_count, summary.sent_count) == (1, 1)
    assert len(telegram.messages) == 1
    async with factory() as session:
        counts_list = []
        for model in (RawItem, EvidenceItem, ContentItem, Notification):
            counts_list.append(
                int((await session.scalar(select(func.count()).select_from(model))) or 0)
            )
        status = await session.scalar(select(Notification.status))
    assert tuple(counts_list) == (1, 1, 1, 1)
    assert status is NotificationStatus.SENT


@pytest.mark.asyncio
async def test_duplicate_content_is_not_pushed_twice(scheduler_runtime) -> None:
    factory, redis = scheduler_runtime
    target = await _target(factory)
    scheduler, telegram = _scheduler(factory, redis, [_response("scheduler-duplicate")])

    first = await scheduler.run(target, limit=1)
    items = await scheduler._feed.recent(1)
    second_claim, second_ids = await scheduler._claim_new(items)

    assert first.status == "PASS"
    assert second_claim == ()
    assert second_ids == ()
    assert len(telegram.messages) == 1
    async with factory() as session:
        notifications = int(
            (await session.scalar(select(func.count()).select_from(Notification))) or 0
        )
    assert notifications == 1


@pytest.mark.asyncio
async def test_telegram_failure_retains_failed_notification(scheduler_runtime) -> None:
    factory, redis = scheduler_runtime
    target = await _target(factory)
    scheduler, telegram = _scheduler(
        factory, redis, [_response("scheduler-failure")], telegram_status=500
    )

    summary = await scheduler.run(target, limit=1)

    assert summary.status == "FAIL"
    assert summary.safe_errors == ("telegram_request_rejected",)
    assert len(telegram.messages) == 1
    async with factory() as session:
        notification = await session.scalar(select(Notification))
    assert notification is not None
    assert notification.status is NotificationStatus.FAILED
    assert notification.failure_code == "telegram_request_rejected"


@pytest.mark.asyncio
async def test_failed_notification_retries_then_sends_without_current_run_item(
    scheduler_runtime,
) -> None:
    factory, redis = scheduler_runtime
    target = await _target(factory)
    first_scheduler, _ = _scheduler(
        factory, redis, [_response("scheduler-retry")], telegram_status=500
    )
    first = await first_scheduler.run(target, limit=1)
    retry_scheduler, telegram = _scheduler(
        factory, redis, [_response("provider-failed-during-retry", 429)]
    )

    retried = await retry_scheduler.run(target, limit=1)

    assert first.status == "FAIL"
    assert retried.status == "PASS"
    assert retried.collection_status != "processed"
    assert retried.new_notification_count == 0
    assert retried.retry_notification_count == 1
    assert retried.sent_count == 1
    assert len(telegram.messages) == 1
    async with factory() as session:
        notification = await session.scalar(select(Notification))
    assert notification is not None
    assert notification.status is NotificationStatus.SENT
    assert notification.retry_count == 1


@pytest.mark.asyncio
async def test_no_new_items_still_delivers_retryable_notification(scheduler_runtime) -> None:
    factory, redis = scheduler_runtime
    target = await _target(factory)
    first_scheduler, _ = _scheduler(
        factory, redis, [_response("scheduler-no-new-retry")], telegram_status=500
    )
    await first_scheduler.run(target, limit=1)
    empty_response = ProviderTransportResponse(
        status_code=200,
        received_at=datetime.now(UTC),
        body={"data": []},
    )
    retry_scheduler, telegram = _scheduler(factory, redis, [empty_response])

    summary = await retry_scheduler.run(target, limit=1)

    assert summary.status == "PASS"
    assert summary.raw_item_count == 0
    assert summary.retry_notification_count == 1
    assert summary.sent_count == 1
    assert len(telegram.messages) == 1


@pytest.mark.asyncio
async def test_failed_notification_at_retry_limit_is_exhausted(scheduler_runtime) -> None:
    factory, redis = scheduler_runtime
    target = await _target(factory)
    scheduler, telegram = _scheduler(
        factory, redis, [_response("scheduler-exhausted")], telegram_status=500
    )
    await scheduler.run(target, limit=1)
    async with factory.begin() as session:
        await session.execute(update(Notification).values(retry_count=MAX_DELIVERY_ATTEMPTS))

    items, notification_ids, exhausted = await scheduler._claim_retries(1)

    assert items == ()
    assert notification_ids == ()
    assert exhausted == 1
    assert len(telegram.messages) == 1


@pytest.mark.asyncio
async def test_stale_sending_is_reclaimed_but_fresh_sending_is_not(
    scheduler_runtime,
) -> None:
    factory, redis = scheduler_runtime
    target = await _target(factory)
    scheduler, _ = _scheduler(factory, redis, [_response("scheduler-stale")])
    await scheduler.run(target, limit=1)
    async with factory.begin() as session:
        await session.execute(
            update(Notification).values(
                status=NotificationStatus.SENDING,
                sent_at=None,
                retry_count=0,
                scheduled_at=datetime.now(UTC),
            )
        )

    fresh_items, fresh_ids, _ = await scheduler._claim_retries(1)
    assert fresh_items == ()
    assert fresh_ids == ()

    async with factory.begin() as session:
        await session.execute(
            update(Notification).values(
                scheduled_at=datetime.now(UTC) - timedelta(seconds=SENDING_STALE_AFTER_SECONDS + 1)
            )
        )
    stale_items, stale_ids, _ = await scheduler._claim_retries(1)

    assert len(stale_items) == 1
    assert len(stale_ids) == 1
    async with factory() as session:
        retry_count = await session.scalar(select(Notification.retry_count))
    assert retry_count == 1


@pytest.mark.asyncio
async def test_concurrent_stale_claim_has_one_winner(scheduler_runtime) -> None:
    factory, redis = scheduler_runtime
    target = await _target(factory)
    scheduler, _ = _scheduler(factory, redis, [_response("scheduler-concurrent")])
    await scheduler.run(target, limit=1)
    async with factory.begin() as session:
        await session.execute(
            update(Notification).values(
                status=NotificationStatus.SENDING,
                sent_at=None,
                retry_count=0,
                scheduled_at=datetime.now(UTC) - timedelta(seconds=SENDING_STALE_AFTER_SECONDS + 1),
            )
        )

    first, second = await asyncio.gather(scheduler._claim_retries(1), scheduler._claim_retries(1))

    assert sum(len(result[1]) for result in (first, second)) == 1


@pytest.mark.asyncio
async def test_provider_failure_writes_no_notification(scheduler_runtime) -> None:
    factory, redis = scheduler_runtime
    target = await _target(factory)
    scheduler, telegram = _scheduler(factory, redis, [_response("scheduler-rate-limit", 429)])

    summary = await scheduler.run(target, limit=1)

    assert summary.status == "FAIL"
    assert summary.sent_count == 0
    assert telegram.messages == []
    async with factory() as session:
        notifications = int(
            (await session.scalar(select(func.count()).select_from(Notification))) or 0
        )
    assert notifications == 0


def test_safe_summary_contains_no_content_or_secret() -> None:
    rendered = str(_summary("PASS", "processed", sent=1).safe_dict()).lower()
    forbidden = (SECRET, "title", "body", "snippet", "description", "https://", "token=")
    assert all(value.lower() not in rendered for value in forbidden)


def test_scheduler_source_has_no_forbidden_dependencies() -> None:
    import market_intelligence.scheduler.marketaux_telegram as module
    import market_intelligence.scheduler.runtime as runtime

    source = (inspect.getsource(module) + inspect.getsource(runtime)).lower()
    forbidden = (
        "local_evaluation",
        "provider_capture",
        "openai",
        "recommendation",
        "formal_dedup",
        "clustering",
        "finnhub",
        "sec_edgar",
        "eia",
        "dotenv",
    )
    assert all(value not in source for value in forbidden)
