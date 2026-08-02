import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from redis.asyncio import Redis
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from market_intelligence.collection.contracts import (
    CollectionTarget,
    FetchBatch,
    FetchRequest,
    RawItemEnvelope,
)
from market_intelligence.collection.errors import ClassifiedCollectionError, CollectionErrorCode
from market_intelligence.collection.locking import retry_marker_key
from market_intelligence.collection.registry import AdapterRegistry, build_fake_registry
from market_intelligence.collection.runner import CollectionRunner, recover_stale_runs
from market_intelligence.collection.scheduler import (
    DispatchRequest,
    dispatch_due_targets,
    dispatch_marker_key,
)
from market_intelligence.core.config import Settings
from market_intelligence.db import Base
from market_intelligence.db.models import (
    AuthorizationStatus,
    CollectionCursor,
    CollectionRun,
    CollectionRunStatus,
    RawItem,
    Source,
    SourceAccount,
    SourceType,
)
from market_intelligence.providers.contracts import (
    ProviderTransportResponse,
    ProviderTransportTimeout,
)
from market_intelligence.providers.marketaux import MarketauxAdapter
from market_intelligence.providers.registry import ProviderAdapterRegistry
from market_intelligence.providers.transport import MockProviderTransport

POSTGRES_TEST_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://market_intelligence:local_dev_only@localhost:5432/market_intelligence",
)
REDIS_TEST_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/13")


@pytest_asyncio.fixture
async def collection_runtime() -> AsyncIterator[tuple[async_sessionmaker[AsyncSession], Redis]]:
    schema = f"spec_0003_test_{uuid.uuid4().hex}"
    engine: AsyncEngine = create_async_engine(
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


async def create_source(
    factory: async_sessionmaker[AsyncSession],
    *,
    authorization_status: AuthorizationStatus = AuthorizationStatus.AUTHORIZED,
    options: dict[str, Any] | None = None,
    access_method: str = "fake",
    enabled: bool = True,
) -> tuple[Source, SourceAccount]:
    async with factory.begin() as session:
        source = Source(
            code=f"fake-{uuid.uuid4().hex}",
            name="Synthetic Fixture",
            source_type=SourceType.RSS,
            access_method=access_method,
            authorization_status=authorization_status,
            retention_class="metadata_only",
            enabled=enabled,
            schedule_seconds=30,
        )
        session.add(source)
        await session.flush()
        account = SourceAccount(
            source_id=source.id,
            identity_status="verified",
            enabled=True,
            collection_options=options or {},
        )
        session.add(account)
        await session.flush()
        source_id, account_id = source.id, account.id
    async with factory() as session:
        return (
            (await session.get(Source, source_id)),
            (await session.get(SourceAccount, account_id)),
        )  # type: ignore[return-value]


def settings() -> Settings:
    return Settings(
        APP_ENV="test",
        COLLECTION_ADAPTER_TIMEOUT_SECONDS=2,
        COLLECTION_TASK_DEADLINE_SECONDS=5,
        COLLECTION_STALE_RUN_AFTER_SECONDS=10,
        COLLECTION_LOCK_TTL_SECONDS=4,
        COLLECTION_BATCH_LIMIT=3,
        _env_file=None,
    )


def provider_runtime(
    response: ProviderTransportResponse | Exception,
) -> tuple[ProviderAdapterRegistry, MockProviderTransport]:
    registry = ProviderAdapterRegistry()
    registry.register("marketaux", MarketauxAdapter())
    return registry, MockProviderTransport([response])


def marketaux_response(
    *, status_code: int = 200, item_id: str = "marketaux-item-1"
) -> ProviderTransportResponse:
    return ProviderTransportResponse(
        status_code=status_code,
        received_at=datetime.now(UTC),
        body={
            "data": [
                {
                    "uuid": item_id,
                    "published_at": "2026-08-02T10:00:00Z",
                    "title": "synthetic fixture",
                    "url": "https://example.invalid/synthetic",
                }
            ]
        },
        headers={},
    )


def make_target(source: Source, account: SourceAccount) -> CollectionTarget:
    return CollectionTarget(
        source.id,
        account.id,
        source.source_type.value,
        source.access_method,
        source.retention_class,
        account.collection_options,
    )


@pytest.mark.asyncio
async def test_runner_persists_raw_item_and_cursor_atomically(
    collection_runtime: tuple[async_sessionmaker[AsyncSession], Redis],
) -> None:
    factory, redis = collection_runtime
    source, account = await create_source(factory, options={"behavior": "items", "pages": 2})
    target = make_target(source, account)
    outcome = await CollectionRunner(factory, redis, build_fake_registry(), settings()).run(target)
    assert outcome.status == CollectionRunStatus.SUCCEEDED.value
    async with factory() as session:
        run = await session.get(CollectionRun, outcome.collection_run_id)
        cursor = await session.scalar(
            select(CollectionCursor).where(CollectionCursor.source_account_id == account.id)
        )
        raw_items = list(
            (
                await session.scalars(
                    select(RawItem).where(RawItem.collection_run_id == outcome.collection_run_id)
                )
            ).all()
        )
        assert run is not None and run.fetched_count == 2
        assert run.new_count == 0 and run.duplicate_count == 0
        assert cursor is not None and cursor.cursor_value == "2"
        assert len(raw_items) == 2


@pytest.mark.asyncio
async def test_runner_uses_provider_registry_and_checkpoints_after_raw_item(
    collection_runtime: tuple[async_sessionmaker[AsyncSession], Redis],
) -> None:
    factory, redis = collection_runtime
    source, account = await create_source(
        factory,
        access_method="marketaux",
        options={"query": "synthetic"},
    )
    provider_registry, transport = provider_runtime(marketaux_response())
    outcome = await CollectionRunner(
        factory,
        redis,
        build_fake_registry(),
        settings(),
        provider_registry=provider_registry,
        provider_transport=transport,
    ).run(make_target(source, account))

    assert outcome.status == CollectionRunStatus.SUCCEEDED.value
    assert len(transport.calls) == 1
    async with factory() as session:
        raw_items = list(
            (
                await session.scalars(
                    select(RawItem).where(RawItem.collection_run_id == outcome.collection_run_id)
                )
            ).all()
        )
        cursor = await session.scalar(
            select(CollectionCursor).where(CollectionCursor.source_account_id == account.id)
        )
        assert len(raw_items) == 1
        assert raw_items[0].external_id == "marketaux-item-1"
        assert cursor is not None
        assert cursor.cursor_type == "provider_cursor_v1"
        assert cursor.cursor_value is not None


@pytest.mark.asyncio
async def test_provider_raw_item_failure_does_not_advance_cursor(
    collection_runtime: tuple[async_sessionmaker[AsyncSession], Redis],
) -> None:
    factory, redis = collection_runtime
    source, account = await create_source(
        factory,
        access_method="marketaux",
        options={"query": "synthetic"},
    )
    provider_registry, transport = provider_runtime(marketaux_response(item_id="x" * 300))
    outcome = await CollectionRunner(
        factory,
        redis,
        build_fake_registry(),
        settings(),
        provider_registry=provider_registry,
        provider_transport=transport,
    ).run(make_target(source, account))

    assert outcome.status == "retry"
    async with factory() as session:
        assert await session.scalar(select(text("count(*)")).select_from(RawItem)) == 0
        assert await session.scalar(select(text("count(*)")).select_from(CollectionCursor)) == 0


@pytest.mark.asyncio
async def test_provider_safe_error_fails_run_without_raw_item(
    collection_runtime: tuple[async_sessionmaker[AsyncSession], Redis],
) -> None:
    factory, redis = collection_runtime
    source, account = await create_source(
        factory,
        access_method="marketaux",
        options={"query": "synthetic"},
    )
    provider_registry, transport = provider_runtime(marketaux_response(status_code=429))
    runner = CollectionRunner(
        factory,
        redis,
        build_fake_registry(),
        settings(),
        provider_registry=provider_registry,
        provider_transport=transport,
    )
    outcome = await runner.run(
        make_target(source, account), attempt=settings().COLLECTION_MAX_RETRIES
    )

    assert outcome.status == "failed"
    async with factory() as session:
        run = await session.get(CollectionRun, outcome.collection_run_id)
        assert run is not None
        assert run.error_code == CollectionErrorCode.RATE_LIMITED.value
        assert await session.scalar(select(text("count(*)")).select_from(RawItem)) == 0


@pytest.mark.asyncio
async def test_provider_timeout_maps_to_safe_collection_error(
    collection_runtime: tuple[async_sessionmaker[AsyncSession], Redis],
) -> None:
    factory, redis = collection_runtime
    source, account = await create_source(
        factory,
        access_method="marketaux",
        options={"query": "synthetic"},
    )
    provider_registry, transport = provider_runtime(ProviderTransportTimeout())
    runner = CollectionRunner(
        factory,
        redis,
        build_fake_registry(),
        settings(),
        provider_registry=provider_registry,
        provider_transport=transport,
    )
    outcome = await runner.run(
        make_target(source, account), attempt=settings().COLLECTION_MAX_RETRIES
    )

    assert outcome.status == "failed"
    async with factory() as session:
        run = await session.get(CollectionRun, outcome.collection_run_id)
        assert run is not None
        assert run.error_code == CollectionErrorCode.TIMEOUT.value
        assert run.error_message_redacted == "provider_request_timed_out"
        assert await session.scalar(select(text("count(*)")).select_from(RawItem)) == 0


@pytest.mark.asyncio
async def test_unauthorized_provider_source_does_not_call_adapter(
    collection_runtime: tuple[async_sessionmaker[AsyncSession], Redis],
) -> None:
    factory, redis = collection_runtime
    source, account = await create_source(
        factory,
        access_method="marketaux",
        authorization_status=AuthorizationStatus.PLANNED,
        options={"query": "synthetic"},
    )
    provider_registry, transport = provider_runtime(marketaux_response())
    runner = CollectionRunner(
        factory,
        redis,
        build_fake_registry(),
        settings(),
        provider_registry=provider_registry,
        provider_transport=transport,
    )
    with pytest.raises(ClassifiedCollectionError) as caught:
        await runner.run(make_target(source, account))
    assert caught.value.code == CollectionErrorCode.CONFIG_INVALID
    assert transport.calls == []
    await assert_no_collection_output(factory)


@pytest.mark.asyncio
async def test_disabled_provider_source_does_not_call_adapter(
    collection_runtime: tuple[async_sessionmaker[AsyncSession], Redis],
) -> None:
    factory, redis = collection_runtime
    source, account = await create_source(
        factory,
        access_method="marketaux",
        enabled=False,
        options={"query": "synthetic"},
    )
    provider_registry, transport = provider_runtime(marketaux_response())
    runner = CollectionRunner(
        factory,
        redis,
        build_fake_registry(),
        settings(),
        provider_registry=provider_registry,
        provider_transport=transport,
    )
    with pytest.raises(ClassifiedCollectionError) as caught:
        await runner.run(make_target(source, account))
    assert caught.value.code == CollectionErrorCode.CONFIG_INVALID
    assert transport.calls == []
    await assert_no_collection_output(factory)


@pytest.mark.asyncio
async def test_retry_reuses_run_id_and_exhaustion_does_not_advance_cursor(
    collection_runtime: tuple[async_sessionmaker[AsyncSession], Redis],
) -> None:
    factory, redis = collection_runtime
    source, account = await create_source(factory, options={"behavior": "error"})
    runner = CollectionRunner(factory, redis, build_fake_registry(), settings())
    first = await runner.run(make_target(source, account), attempt=0)
    assert first.status == "retry"
    assert first.collection_run_id is not None
    exhausted = await runner.run(
        make_target(source, account),
        collection_run_id=first.collection_run_id,
        attempt=settings().COLLECTION_MAX_RETRIES,
    )
    assert exhausted.collection_run_id == first.collection_run_id
    assert exhausted.status == "failed"
    async with factory() as session:
        run = await session.get(CollectionRun, first.collection_run_id)
        cursor_count = await session.scalar(select(text("count(*)")).select_from(CollectionCursor))
        assert run is not None and run.status == CollectionRunStatus.FAILED
        assert run.error_code == "COLLECTION_UPSTREAM_RETRYABLE"
        assert cursor_count == 0


class InvalidCursorAdapter:
    cursor_type: str | None = "fake_sequence"

    async def fetch(self, request: FetchRequest) -> FetchBatch:
        return FetchBatch(
            items=(
                RawItemEnvelope(
                    "synthetic",
                    datetime.now(UTC),
                    200,
                    "application/x.fake",
                    None,
                    "a" * 64,
                    request.target.retention_class,
                ),
            ),
            next_cursor="9",
        )

    def is_cursor_successor(self, current: str | None, candidate: str) -> bool:
        del current, candidate
        return False


@pytest.mark.asyncio
async def test_invalid_checkpoint_fails_without_raw_item_or_cursor(
    collection_runtime: tuple[async_sessionmaker[AsyncSession], Redis],
) -> None:
    factory, redis = collection_runtime
    source, account = await create_source(factory)
    registry = AdapterRegistry()
    registry.register("fake", InvalidCursorAdapter())
    outcome = await CollectionRunner(factory, redis, registry, settings()).run(
        make_target(source, account)
    )
    assert outcome.status == "failed"
    async with factory() as session:
        raw_count = await session.scalar(select(text("count(*)")).select_from(RawItem))
        cursor_count = await session.scalar(select(text("count(*)")).select_from(CollectionCursor))
        assert raw_count == 0
        assert cursor_count == 0


@pytest.mark.asyncio
async def test_unknown_access_method_fails_closed_before_run(
    collection_runtime: tuple[async_sessionmaker[AsyncSession], Redis],
) -> None:
    factory, redis = collection_runtime
    source, account = await create_source(factory)
    invalid = CollectionTarget(
        source.id,
        account.id,
        source.source_type.value,
        "web",
        source.retention_class,
        {},
    )
    with pytest.raises(ClassifiedCollectionError) as caught:
        await CollectionRunner(factory, redis, build_fake_registry(), settings()).run(invalid)
    assert caught.value.code == CollectionErrorCode.CONFIG_INVALID
    async with factory() as session:
        assert await session.scalar(select(text("count(*)")).select_from(CollectionRun)) == 0


async def assert_no_collection_output(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with factory() as session:
        assert await session.scalar(select(text("count(*)")).select_from(CollectionRun)) == 0
        assert await session.scalar(select(text("count(*)")).select_from(RawItem)) == 0


@pytest.mark.asyncio
async def test_disabled_account_fails_closed_without_output(
    collection_runtime: tuple[async_sessionmaker[AsyncSession], Redis],
) -> None:
    factory, redis = collection_runtime
    source, account = await create_source(factory)
    async with factory.begin() as session:
        stored = await session.get(SourceAccount, account.id)
        assert stored is not None
        stored.enabled = False
    with pytest.raises(ClassifiedCollectionError) as caught:
        await CollectionRunner(factory, redis, build_fake_registry(), settings()).run(
            make_target(source, account)
        )
    assert caught.value.code == CollectionErrorCode.CONFIG_INVALID
    await assert_no_collection_output(factory)


@pytest.mark.asyncio
async def test_mismatched_account_fails_closed_without_output(
    collection_runtime: tuple[async_sessionmaker[AsyncSession], Redis],
) -> None:
    factory, redis = collection_runtime
    source, _ = await create_source(factory)
    _, other_account = await create_source(factory)
    invalid = CollectionTarget(
        source.id,
        other_account.id,
        source.source_type.value,
        "fake",
        source.retention_class,
        {},
    )
    with pytest.raises(ClassifiedCollectionError) as caught:
        await CollectionRunner(factory, redis, build_fake_registry(), settings()).run(invalid)
    assert caught.value.code == CollectionErrorCode.CONFIG_INVALID
    await assert_no_collection_output(factory)


@pytest.mark.asyncio
async def test_source_level_target_with_account_fails_closed_without_output(
    collection_runtime: tuple[async_sessionmaker[AsyncSession], Redis],
) -> None:
    factory, redis = collection_runtime
    source, _ = await create_source(factory)
    invalid = CollectionTarget(
        source.id,
        None,
        source.source_type.value,
        "fake",
        source.retention_class,
        {},
    )
    with pytest.raises(ClassifiedCollectionError) as caught:
        await CollectionRunner(factory, redis, build_fake_registry(), settings()).run(invalid)
    assert caught.value.code == CollectionErrorCode.CONFIG_INVALID
    await assert_no_collection_output(factory)


@pytest.mark.asyncio
async def test_dispatcher_only_schedules_authorized_or_implemented(
    collection_runtime: tuple[async_sessionmaker[AsyncSession], Redis],
) -> None:
    factory, redis = collection_runtime
    for status in AuthorizationStatus:
        await create_source(factory, authorization_status=status)
    captured: list[DispatchRequest] = []

    async def enqueue(request: DispatchRequest) -> None:
        captured.append(request)

    await asyncio.gather(
        dispatch_due_targets(
            factory,
            build_fake_registry(),
            redis,
            enqueue,
            execution_window_seconds=1920,
            now=datetime(2026, 7, 29, tzinfo=UTC),
        ),
        dispatch_due_targets(
            factory,
            build_fake_registry(),
            redis,
            enqueue,
            execution_window_seconds=1920,
            now=datetime(2026, 7, 29, tzinfo=UTC),
        ),
    )
    assert len(captured) == 2
    marker_ttls = [await redis.ttl(dispatch_marker_key(request.task_id)) for request in captured]
    assert all(ttl >= 1920 for ttl in marker_ttls)


async def add_second_account(
    factory: async_sessionmaker[AsyncSession], source_id: uuid.UUID
) -> SourceAccount:
    async with factory.begin() as session:
        account = SourceAccount(
            source_id=source_id,
            identity_status="verified",
            enabled=True,
            collection_options={"behavior": "empty"},
        )
        session.add(account)
        await session.flush()
        account_id = account.id
    async with factory() as session:
        stored = await session.get(SourceAccount, account_id)
        assert stored is not None
        return stored


@pytest.mark.asyncio
async def test_account_success_does_not_use_other_account_old_success(
    collection_runtime: tuple[async_sessionmaker[AsyncSession], Redis],
) -> None:
    factory, redis = collection_runtime
    source, account_a = await create_source(factory)
    account_b = await add_second_account(factory, source.id)
    old_success = datetime.now(UTC) - timedelta(days=1)
    async with factory.begin() as session:
        stored_source = await session.get(Source, source.id)
        assert stored_source is not None
        stored_source.consecutive_failures = 3
        session.add(
            CollectionRun(
                source_id=source.id,
                source_account_id=account_b.id,
                started_at=old_success,
                finished_at=old_success,
                status=CollectionRunStatus.SUCCEEDED,
            )
        )
    outcome = await CollectionRunner(factory, redis, build_fake_registry(), settings()).run(
        make_target(source, account_a)
    )
    assert outcome.status == "succeeded"
    async with factory() as session:
        stored_source = await session.get(Source, source.id)
        assert stored_source is not None
        assert stored_source.last_success_at is None
        assert stored_source.consecutive_failures == 3


@pytest.mark.asyncio
async def test_account_success_does_not_mask_other_account_current_failure(
    collection_runtime: tuple[async_sessionmaker[AsyncSession], Redis],
) -> None:
    factory, redis = collection_runtime
    source, account_a = await create_source(factory)
    account_b = await add_second_account(factory, source.id)
    now = datetime.now(UTC)
    async with factory.begin() as session:
        stored_source = await session.get(Source, source.id)
        assert stored_source is not None
        stored_source.consecutive_failures = 2
        session.add(
            CollectionRun(
                source_id=source.id,
                source_account_id=account_b.id,
                started_at=now,
                finished_at=now,
                status=CollectionRunStatus.FAILED,
                error_code="COLLECTION_UNKNOWN",
            )
        )
    outcome = await CollectionRunner(factory, redis, build_fake_registry(), settings()).run(
        make_target(source, account_a)
    )
    assert outcome.status == "succeeded"
    async with factory() as session:
        stored_source = await session.get(Source, source.id)
        assert stored_source is not None
        assert stored_source.last_success_at is None
        assert stored_source.consecutive_failures == 2


@pytest.mark.asyncio
async def test_stale_recovery_marks_error_without_advancing_cursor(
    collection_runtime: tuple[async_sessionmaker[AsyncSession], Redis],
) -> None:
    factory, redis = collection_runtime
    source, account = await create_source(factory)
    now = datetime.now(UTC)
    async with factory.begin() as session:
        cursor = CollectionCursor(
            source_account_id=account.id,
            cursor_type="fake_sequence",
            cursor_value="7",
        )
        run = CollectionRun(
            source_id=source.id,
            source_account_id=account.id,
            started_at=now - timedelta(minutes=30),
            status=CollectionRunStatus.RUNNING,
        )
        session.add_all([cursor, run])
        await session.flush()
        run_id = run.id
    assert await recover_stale_runs(factory, redis, 900, now=now) == 1
    async with factory() as session:
        recovered = await session.get(CollectionRun, run_id)
        cursor_value = await session.scalar(
            select(CollectionCursor.cursor_value).where(
                CollectionCursor.source_account_id == account.id
            )
        )
        assert recovered is not None
        assert recovered.status == CollectionRunStatus.FAILED
        assert recovered.error_code == "COLLECTION_STALE_RUN"
        assert cursor_value == "7"


@pytest.mark.asyncio
async def test_stale_recovery_preserves_run_with_scheduled_retry(
    collection_runtime: tuple[async_sessionmaker[AsyncSession], Redis],
) -> None:
    factory, redis = collection_runtime
    source, account = await create_source(factory)
    now = datetime.now(UTC)
    async with factory.begin() as session:
        run = CollectionRun(
            source_id=source.id,
            source_account_id=account.id,
            started_at=now - timedelta(minutes=30),
            status=CollectionRunStatus.RUNNING,
        )
        session.add(run)
        await session.flush()
        run_id = run.id
    await redis.set(retry_marker_key(str(run_id)), "scheduled", ex=60)
    assert await recover_stale_runs(factory, redis, 900, now=now) == 0
    async with factory() as session:
        preserved = await session.get(CollectionRun, run_id)
        assert preserved is not None
        assert preserved.status == CollectionRunStatus.RUNNING
