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
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from market_intelligence.collection.contracts import CollectionTarget
from market_intelligence.core.config import Settings
from market_intelligence.db import Base, ContentItem, EvidenceItem, RawItem
from market_intelligence.db.models import AuthorizationStatus, Source, SourceAccount, SourceType
from market_intelligence.evidence.end_to_end import EndToEndStatus
from market_intelligence.pipeline.multi_provider_ingestion import MultiProviderIngestionPipeline
from market_intelligence.providers.contracts import ProviderFetchRequest, ProviderTransportResponse
from market_intelligence.providers.credentials import RuntimeCredential
from market_intelligence.providers.eia import EiaAdapter
from market_intelligence.providers.finnhub import FinnhubAdapter
from market_intelligence.providers.sec_edgar import SecEdgarAdapter
from market_intelligence.providers.transport import MockProviderTransport

POSTGRES_TEST_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://market_intelligence:local_dev_only@localhost:5432/market_intelligence",
)
REDIS_TEST_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/13")
SECRET = "synthetic-provider-secret"


@pytest_asyncio.fixture
async def provider_runtime() -> AsyncIterator[tuple[async_sessionmaker[AsyncSession], Redis]]:
    schema = f"spec_0036_{uuid.uuid4().hex}"
    engine = create_async_engine(
        POSTGRES_TEST_URL, connect_args={"server_settings": {"search_path": schema}}
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


def _settings(limit: int = 2) -> Settings:
    return Settings(
        APP_ENV="test",
        COLLECTION_ADAPTER_TIMEOUT_SECONDS=2,
        COLLECTION_TASK_DEADLINE_SECONDS=5,
        COLLECTION_LOCK_TTL_SECONDS=4,
        COLLECTION_BATCH_LIMIT=limit,
        _env_file=None,
    )


async def _target(factory, provider: str, options: dict[str, object]) -> CollectionTarget:
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
        account = SourceAccount(
            source_id=source.id,
            identity_status="verified",
            enabled=True,
            collection_options=options,
        )
        session.add(account)
        await session.flush()
        return CollectionTarget(
            source.id,
            account.id,
            source.source_type.value,
            provider,
            source.retention_class,
            options,
        )


def _response(provider: str, status: int = 200) -> ProviderTransportResponse:
    bodies = {
        "finnhub": {"c": 100.0, "d": 1.0, "h": 102.0, "l": 98.0, "t": 1786300000},
        "eia": {
            "response": {
                "data": [
                    {
                        "period": "2026-07",
                        "price": 12.3,
                        "stateid": "US",
                        "sectorid": "ALL",
                    }
                ]
            }
        },
        "sec_edgar": {
            "filings": {
                "recent": {
                    "accessionNumber": ["0000320193-26-000001"],
                    "filingDate": ["2026-08-01"],
                    "form": ["10-Q"],
                    "primaryDocument": ["synthetic.htm"],
                }
            }
        },
    }
    return ProviderTransportResponse(status, datetime.now(UTC), bodies[provider])


def _adapter(provider: str):
    if provider == "finnhub":
        return FinnhubAdapter(RuntimeCredential("FINNHUB_API_KEY", SECRET))
    if provider == "eia":
        return EiaAdapter(RuntimeCredential("EIA_API_KEY", SECRET))
    return SecEdgarAdapter(RuntimeCredential("SEC_USER_AGENT", "synthetic contact"))


@pytest.mark.parametrize(
    ("provider", "options", "content_count"),
    [
        ("finnhub", {"symbol": "AAPL"}, 0),
        ("eia", {"dataset": "electricity"}, 0),
        ("sec_edgar", {"ticker": "AAPL", "cik": "0000320193"}, 1),
    ],
)
@pytest.mark.asyncio
async def test_mock_provider_ingestion_writes_raw_and_evidence(
    provider_runtime, provider, options, content_count
) -> None:
    factory, redis = provider_runtime
    target = await _target(factory, provider, options)
    transport = MockProviderTransport([_response(provider)])
    pipeline = MultiProviderIngestionPipeline(
        factory, redis, _settings(1), _adapter(provider), transport
    )

    outcome = await pipeline.run(target)

    assert outcome.status is EndToEndStatus.PROCESSED
    assert outcome.raw_item_count == 1
    assert len(outcome.trigger_outcomes) == 1
    assert outcome.trigger_outcomes[0].pipeline_outcome is not None
    assert outcome.trigger_outcomes[0].pipeline_outcome.evidence_item_id is not None
    async with factory() as session:
        counts_list = []
        for model in (RawItem, EvidenceItem, ContentItem):
            counts_list.append(
                int((await session.scalar(select(func.count()).select_from(model))) or 0)
            )
        counts = tuple(counts_list)
    assert counts == (1, 1, content_count)
    assert len(transport.calls) == 1
    assert SECRET not in repr(transport.calls[0])


@pytest.mark.parametrize("provider", ["finnhub", "eia", "sec_edgar"])
@pytest.mark.asyncio
async def test_provider_error_writes_nothing(provider_runtime, provider) -> None:
    factory, redis = provider_runtime
    options = {
        "finnhub": {"symbol": "AAPL"},
        "eia": {"dataset": "electricity"},
        "sec_edgar": {"ticker": "AAPL", "cik": "0000320193"},
    }[provider]
    target = await _target(factory, provider, options)
    pipeline = MultiProviderIngestionPipeline(
        factory,
        redis,
        _settings(1),
        _adapter(provider),
        MockProviderTransport([_response(provider, 500)]),
    )

    outcome = await pipeline.run(target)

    assert outcome.status is EndToEndStatus.COLLECTION_FAILED
    async with factory() as session:
        assert int((await session.scalar(select(func.count()).select_from(RawItem))) or 0) == 0
        assert int((await session.scalar(select(func.count()).select_from(EvidenceItem))) or 0) == 0


@pytest.mark.parametrize("provider", ["finnhub", "eia", "sec_edgar"])
@pytest.mark.asyncio
async def test_adapter_contract_rejects_malformed_response(provider) -> None:
    adapter = _adapter(provider)
    request = ProviderFetchRequest(
        uuid.uuid4(),
        uuid.uuid4(),
        None,
        {
            "finnhub": {"symbol": "AAPL"},
            "eia": {"dataset": "electricity"},
            "sec_edgar": {"ticker": "AAPL", "cik": "0000320193"},
        }[provider],
        1,
        datetime.now(UTC) + timedelta(seconds=30),
        "synthetic",
    )
    result = await adapter.fetch(
        request,
        MockProviderTransport(
            [ProviderTransportResponse(200, datetime.now(UTC), {"malformed": True})]
        ),
    )
    assert result.raw_items == ()
    assert result.safe_errors[0].safe_message == "provider_response_shape_invalid"


@pytest.mark.parametrize(
    "script",
    ["finnhub_ingestion_smoke.py", "eia_ingestion_smoke.py", "sec_edgar_ingestion_smoke.py"],
)
def test_default_smoke_is_inert_and_safe(script: str) -> None:
    completed = subprocess.run(
        [sys.executable, f"scripts/{script}"],
        check=False,
        capture_output=True,
        text=True,
        env={"PATH": os.environ.get("PATH", "")},
    )
    report = json.loads(completed.stdout)
    assert completed.returncode == 0
    assert report["status"] == "DRY_RUN"
    assert report["credential_read"] is False
    assert report["request_enabled"] is False
    assert report["db_written"] is False
    rendered = completed.stdout.lower()
    assert all(value not in rendered for value in (SECRET, "title", "body", "snippet", "token="))


def test_multi_provider_source_audit() -> None:
    import market_intelligence.pipeline.multi_provider_ingestion as pipeline
    import market_intelligence.providers.eia as eia
    import market_intelligence.providers.finnhub as finnhub
    import market_intelligence.providers.sec_edgar as sec

    source = "".join(inspect.getsource(module).lower() for module in (pipeline, eia, finnhub, sec))
    forbidden = (
        "local_evaluation",
        "provider_capture",
        "openai",
        "recommendation",
        "scheduler",
        "telegram",
        "event",
        "clustering",
        "dotenv",
    )
    assert all(value not in source for value in forbidden)
