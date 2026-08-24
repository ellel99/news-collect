import inspect
import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from redis.asyncio import Redis
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from market_intelligence.collection.contracts import CollectionTarget
from market_intelligence.collection.registry import build_fake_registry
from market_intelligence.collection.runner import CollectionRunner
from market_intelligence.core.config import Settings
from market_intelligence.db import Base, EvidenceItem, RawItem
from market_intelligence.db.models import AuthorizationStatus, Source, SourceAccount, SourceType
from market_intelligence.evidence.end_to_end import (
    EndToEndMockEvidencePipeline,
    EndToEndStatus,
    InMemoryProviderProjectionSidecar,
)
from market_intelligence.evidence.orchestration import EvidencePipelineStatus
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
async def e2e_runtime() -> AsyncIterator[tuple[async_sessionmaker[AsyncSession], Redis]]:
    schema = f"spec_0029_e2e_{uuid.uuid4().hex}"
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


async def _source(
    factory: async_sessionmaker[AsyncSession],
    *,
    authorized: bool = True,
    enabled: bool = True,
) -> tuple[Source, SourceAccount]:
    async with factory.begin() as session:
        source = Source(
            code=f"e2e-{uuid.uuid4().hex}",
            name="Synthetic E2E Fixture",
            source_type=SourceType.API,
            access_method="marketaux",
            authorization_status=(
                AuthorizationStatus.AUTHORIZED if authorized else AuthorizationStatus.PLANNED
            ),
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
            collection_options={"query": "synthetic"},
        )
        session.add(account)
        await session.flush()
        source_id, account_id = source.id, account.id
    async with factory() as session:
        return (
            await session.get(Source, source_id),  # type: ignore[return-value]
            await session.get(SourceAccount, account_id),  # type: ignore[return-value]
        )


def _response(*, status: int = 200, item_id: str = "synthetic-marketaux-item"):
    return ProviderTransportResponse(
        status_code=status,
        received_at=datetime.now(UTC),
        body={
            "data": [
                {
                    "uuid": item_id,
                    "published_at": "2026-08-03T10:00:00Z",
                    "title": "content must not reach outcomes",
                    "description": "content must not reach outcomes",
                    "url": "https://example.invalid/not-retained",
                }
            ]
        },
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


def _pipeline(factory, redis, response, sidecar=None):
    registry = ProviderAdapterRegistry()
    registry.register("marketaux", MarketauxAdapter())
    transport = MockProviderTransport(list(response) if isinstance(response, tuple) else [response])
    sidecar = sidecar or InMemoryProviderProjectionSidecar()
    runner = CollectionRunner(
        factory,
        redis,
        build_fake_registry(),
        _settings(),
        provider_registry=registry,
        provider_transport=transport,
        provider_result_observer=sidecar,
    )
    return EndToEndMockEvidencePipeline(factory, runner, sidecar), transport


def _target(source: Source, account: SourceAccount) -> CollectionTarget:
    return CollectionTarget(
        source.id,
        account.id,
        source.source_type.value,
        source.access_method,
        source.retention_class,
        account.collection_options,
    )


async def _counts(factory) -> tuple[int, int]:
    async with factory() as session:
        raw = int((await session.scalar(select(func.count()).select_from(RawItem))) or 0)
        evidence = int((await session.scalar(select(func.count()).select_from(EvidenceItem))) or 0)
        return raw, evidence


@pytest.mark.asyncio
async def test_mock_collection_reaches_evidence_write_path(e2e_runtime) -> None:
    factory, redis = e2e_runtime
    source, account = await _source(factory)
    pipeline, transport = _pipeline(factory, redis, _response())

    outcome = await pipeline.run(_target(source, account))

    assert outcome.status is EndToEndStatus.PROCESSED
    assert len(outcome.trigger_outcomes) == 1
    assert outcome.trigger_outcomes[0].pipeline_outcome is not None
    assert outcome.trigger_outcomes[0].pipeline_outcome.status is EvidencePipelineStatus.WRITTEN
    assert await _counts(factory) == (1, 1)
    assert len(transport.calls) == 1
    rendered = repr(outcome).lower()
    assert all(
        value not in rendered
        for value in (
            "content must not reach outcomes",
            "example.invalid",
            "title",
            "description",
            "snippet",
            "api_key",
            "authorization",
        )
    )


@pytest.mark.asyncio
async def test_reprocessing_run_is_idempotent(e2e_runtime) -> None:
    factory, redis = e2e_runtime
    source, account = await _source(factory)
    pipeline, _ = _pipeline(factory, redis, _response())
    first = await pipeline.run(_target(source, account))
    assert first.collection_run_id is not None

    second = await pipeline.process_run(first.collection_run_id)

    assert second.trigger_outcomes[0].pipeline_outcome is not None
    assert second.trigger_outcomes[0].pipeline_outcome.status is EvidencePipelineStatus.DUPLICATE
    assert await _counts(factory) == (1, 1)


@pytest.mark.asyncio
async def test_invalid_provider_identity_never_writes_raw_or_evidence(e2e_runtime) -> None:
    factory, redis = e2e_runtime
    source, account = await _source(factory)
    pipeline, _ = _pipeline(factory, redis, _response(item_id="x" * 300))

    outcome = await pipeline.run(_target(source, account))

    assert outcome.status is EndToEndStatus.COLLECTION_FAILED
    assert outcome.retry_delay is None
    assert await _counts(factory) == (0, 0)


@pytest.mark.asyncio
async def test_missing_projection_never_writes_evidence(e2e_runtime) -> None:
    factory, redis = e2e_runtime
    source, account = await _source(factory)
    observed = InMemoryProviderProjectionSidecar()
    pipeline, _ = _pipeline(factory, redis, _response(), observed)
    pipeline._sidecar = InMemoryProviderProjectionSidecar()

    outcome = await pipeline.run(_target(source, account))

    assert outcome.status is EndToEndStatus.INVALID
    assert outcome.safe_errors[0].code == "projection_sidecar_missing"
    assert await _counts(factory) == (1, 0)


class _MismatchedSidecar(InMemoryProviderProjectionSidecar):
    def bind(self, raw_item):
        projection = super().bind(raw_item)
        return None if projection is None else replace(projection, source_id=uuid.uuid4())


@pytest.mark.asyncio
async def test_projection_mismatch_never_writes_evidence(e2e_runtime) -> None:
    factory, redis = e2e_runtime
    source, account = await _source(factory)
    sidecar = _MismatchedSidecar()
    pipeline, _ = _pipeline(factory, redis, _response(), sidecar)

    outcome = await pipeline.run(_target(source, account))

    assert outcome.trigger_outcomes[0].safe_errors[0].code == "projection_mismatch"
    assert await _counts(factory) == (1, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [_response(status=429), ProviderTransportTimeout()])
async def test_provider_failure_never_writes_evidence(e2e_runtime, response) -> None:
    factory, redis = e2e_runtime
    source, account = await _source(factory)
    pipeline, _ = _pipeline(factory, redis, response)

    outcome = await pipeline.run(_target(source, account))

    assert outcome.status is EndToEndStatus.COLLECTION_FAILED
    assert await _counts(factory) == (0, 0)


@pytest.mark.asyncio
async def test_retry_continues_same_collection_run(e2e_runtime) -> None:
    factory, redis = e2e_runtime
    source, account = await _source(factory)
    pipeline, transport = _pipeline(
        factory,
        redis,
        (_response(status=429), _response(item_id="retry-success")),
    )
    target = _target(source, account)

    first = await pipeline.run(target)
    second = await pipeline.run(
        target,
        collection_run_id=first.collection_run_id,
        attempt=1,
    )

    assert first.status is EndToEndStatus.COLLECTION_FAILED
    assert first.retry_delay is not None
    assert second.status is EndToEndStatus.PROCESSED
    assert second.collection_run_id == first.collection_run_id
    assert await _counts(factory) == (1, 1)
    assert len(transport.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("authorized,enabled", [(False, True), (True, False)])
async def test_source_gate_prevents_transport_and_evidence(
    e2e_runtime, authorized, enabled
) -> None:
    factory, redis = e2e_runtime
    source, account = await _source(factory, authorized=authorized, enabled=enabled)
    pipeline, transport = _pipeline(factory, redis, _response())

    outcome = await pipeline.run(_target(source, account))

    assert outcome.status is EndToEndStatus.COLLECTION_FAILED
    assert transport.calls == []
    assert await _counts(factory) == (0, 0)


def test_end_to_end_boundary_has_no_forbidden_runtime_dependencies() -> None:
    import market_intelligence.evidence.end_to_end as module

    source = inspect.getsource(module).lower()
    forbidden = (
        "requests",
        "httpx",
        "urllib",
        "provider_capture",
        "local_evaluation",
        "scheduler",
        "openai",
        "telegram",
        "recommendation",
    )
    assert all(term not in source for term in forbidden)
