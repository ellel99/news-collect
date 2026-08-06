import inspect
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from redis.asyncio import Redis
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from market_intelligence.collection.contracts import CollectionTarget
from market_intelligence.core.config import Settings
from market_intelligence.db import Base, ContentItem
from market_intelligence.db.models import AuthorizationStatus, Source, SourceAccount, SourceType
from market_intelligence.feed.marketaux_feed import MarketauxFeedService
from market_intelligence.pipeline.marketaux_real_collection import MarketauxRealCollectionPipeline
from market_intelligence.providers.contracts import ProviderTransportResponse
from market_intelligence.providers.credentials import RuntimeCredential
from market_intelligence.providers.transport import MockProviderTransport

POSTGRES_TEST_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://market_intelligence:local_dev_only@localhost:5432/market_intelligence",
)
REDIS_TEST_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/13")


@pytest_asyncio.fixture
async def feed_runtime() -> AsyncIterator[tuple[async_sessionmaker[AsyncSession], Redis]]:
    schema = f"spec_0033_feed_{uuid.uuid4().hex}"
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
            code=f"spec0033-{uuid.uuid4().hex}",
            name="Marketaux",
            source_type=SourceType.API,
            access_method="marketaux",
            authorization_status=AuthorizationStatus.AUTHORIZED,
            retention_class="metadata_only",
            enabled=True,
            schedule_seconds=None,
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


@pytest.mark.asyncio
async def test_collected_marketaux_item_becomes_visible_content(feed_runtime) -> None:
    factory, redis = feed_runtime
    target = await _target(factory)
    response = ProviderTransportResponse(
        status_code=200,
        received_at=datetime(2026, 8, 6, 1, 0, tzinfo=UTC),
        body={
            "data": [
                {
                    "uuid": "visible-item-1",
                    "published_at": "2026-08-06T00:30:00Z",
                    "title": "Synthetic visible headline",
                    "description": "must not be persisted",
                    "snippet": "must not be persisted",
                    "url": "https://example.invalid/visible-item-1",
                }
            ]
        },
    )
    pipeline = MarketauxRealCollectionPipeline(
        factory,
        redis,
        _settings(),
        RuntimeCredential("MARKETAUX_API_TOKEN", "synthetic-secret"),
        MockProviderTransport([response]),
    )

    outcome = await pipeline.run(target)
    items = await MarketauxFeedService(factory).recent(5)

    assert outcome.status == "processed"
    assert len(items) == 1
    item = items[0]
    assert item.title == "Synthetic visible headline"
    assert item.source == "Marketaux"
    assert item.provider == "marketaux"
    assert item.canonical_url == "https://example.invalid/visible-item-1"
    assert item.provider_item_id == "visible-item-1"
    assert item.evidence_item_id is not None
    async with factory() as session:
        content = await session.scalar(select(ContentItem))
        count = await session.scalar(select(func.count()).select_from(ContentItem))
    assert count == 1
    assert content is not None
    assert content.body is None
    assert content.source_summary is None


@pytest.mark.asyncio
async def test_recent_feed_is_ordered_and_bounded(feed_runtime) -> None:
    factory, redis = feed_runtime
    target = await _target(factory)
    responses = [
        ProviderTransportResponse(
            status_code=200,
            received_at=datetime(2026, 8, 6, hour, tzinfo=UTC),
            body={
                "data": [
                    {
                        "uuid": f"visible-{hour}",
                        "published_at": f"2026-08-06T0{hour}:00:00Z",
                        "title": f"Synthetic headline {hour}",
                        "url": f"https://example.invalid/visible-{hour}",
                    }
                ]
            },
        )
        for hour in (1, 2)
    ]
    pipeline = MarketauxRealCollectionPipeline(
        factory,
        redis,
        _settings(),
        RuntimeCredential("MARKETAUX_API_TOKEN", "synthetic-secret"),
        MockProviderTransport(responses),
    )
    await pipeline.run(target)
    await pipeline.run(target)

    items = await MarketauxFeedService(factory).recent(1)

    assert len(items) == 1
    assert items[0].provider_item_id == "visible-2"


def test_visible_feed_source_has_no_forbidden_dependencies() -> None:
    import market_intelligence.feed.marketaux_feed as module

    source = inspect.getsource(module).lower()
    forbidden = (
        "requests",
        "httpx",
        "urllib.request",
        "local_evaluation",
        "provider_capture",
        "scheduler",
        "openai",
        "recommendation",
        "dedup",
        "import event",
        "telegram",
    )
    assert all(term not in source for term in forbidden)
