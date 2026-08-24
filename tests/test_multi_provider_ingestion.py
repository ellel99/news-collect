import inspect
import json
import os
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import pytest_asyncio
from redis.asyncio import Redis
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from market_intelligence.collection.contracts import CollectionTarget
from market_intelligence.core.config import Settings
from market_intelligence.db import Base, ContentItem, EvidenceItem, RawItem
from market_intelligence.db.models import (
    AuthorizationStatus,
    CollectionCursor,
    CollectionRun,
    CollectionRunStatus,
    Source,
    SourceAccount,
    SourceType,
)
from market_intelligence.evidence.end_to_end import EndToEndStatus
from market_intelligence.pipeline.multi_provider_ingestion import (
    MultiProviderIngestionPipeline,
    ProviderTargetError,
    bootstrap_provider_target,
    diagnose_provider_target,
)
from market_intelligence.pipeline.provider_runtime import execute_provider, target_summary
from market_intelligence.providers.contracts import (
    ProviderFetchRequest,
    ProviderTransportRequest,
    ProviderTransportResponse,
)
from market_intelligence.providers.credentials import RuntimeCredential
from market_intelligence.providers.eia import EiaAdapter
from market_intelligence.providers.finnhub import FinnhubAdapter
from market_intelligence.providers.http_transport import HttpxProviderTransport
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
        "finnhub": {
            "c": 100.0,
            "d": 1.0,
            "dp": 1.01,
            "h": 102.0,
            "l": 98.0,
            "o": 99.0,
            "pc": 99.0,
            "t": 1786300000,
        },
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


def _sec_response(accession: str, filing_date: str) -> ProviderTransportResponse:
    return ProviderTransportResponse(
        200,
        datetime.now(UTC),
        {
            "filings": {
                "recent": {
                    "accessionNumber": [accession],
                    "filingDate": [filing_date],
                    "form": ["10-Q"],
                    "primaryDocument": ["synthetic.htm"],
                }
            }
        },
    )


def _eia_response(period: str) -> ProviderTransportResponse:
    return ProviderTransportResponse(
        200,
        datetime.now(UTC),
        {
            "response": {
                "data": [
                    {
                        "period": period,
                        "price": 12.3,
                        "stateid": "AK",
                        "sectorid": "ALL",
                    }
                ]
            }
        },
    )


def _multi_row_response(provider: str) -> ProviderTransportResponse:
    if provider == "eia":
        body = {
            "response": {
                "data": [
                    {"period": "2026-08", "price": 1, "stateid": "US", "sectorid": "ALL"},
                    {"period": "2026-07", "price": 2, "stateid": "US", "sectorid": "ALL"},
                ]
            }
        }
    else:
        body = {
            "filings": {
                "recent": {
                    "accessionNumber": ["0000320193-26-000002", "0000320193-26-000001"],
                    "filingDate": ["2026-08-02", "2026-08-01"],
                    "form": ["10-Q", "8-K"],
                    "primaryDocument": ["new.htm", "old.htm"],
                }
            }
        }
    return ProviderTransportResponse(200, datetime.now(UTC), body)


def _adapter(provider: str):
    if provider == "finnhub":
        return FinnhubAdapter(RuntimeCredential("FINNHUB_API_KEY", SECRET))
    if provider == "eia":
        return EiaAdapter(RuntimeCredential("EIA_API_KEY", SECRET))
    return SecEdgarAdapter(RuntimeCredential("SEC_USER_AGENT", "synthetic contact"))


@pytest.mark.parametrize(
    ("provider", "options", "content_count"),
    [
        ("finnhub", {"symbol": "AAPL"}, 1),
        ("eia", {"dataset": "electricity"}, 1),
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


@pytest.mark.asyncio
async def test_sec_snapshot_polling_initial_same_newer_and_older(provider_runtime) -> None:
    factory, redis = provider_runtime
    target = await _target(factory, "sec_edgar", {"ticker": "AAPL", "cik": "0000320193"})
    filing_a = "0000320193-26-000001"
    filing_b = "0000320193-26-000002"
    filing_z = "0000320193-25-000099"
    transport = MockProviderTransport(
        [
            _sec_response(filing_a, "2026-08-01"),
            _sec_response(filing_a, "2026-08-01"),
            _sec_response(filing_a, "2026-08-01"),
            _sec_response(filing_a, "2026-08-01"),
            _sec_response(filing_b, "2026-08-02"),
            _sec_response(filing_z, "2026-07-01"),
        ]
    )
    pipeline = MultiProviderIngestionPipeline(
        factory, redis, _settings(1), _adapter("sec_edgar"), transport
    )

    initial = await pipeline.run(target)
    repeated = [await pipeline.run(target) for _ in range(3)]
    newer = await pipeline.run(target)
    older = await pipeline.run(target)

    assert initial.status is EndToEndStatus.PROCESSED and initial.raw_item_count == 1
    assert all(
        outcome.status is EndToEndStatus.PROCESSED
        and outcome.raw_item_count == 0
        and outcome.safe_errors == ()
        for outcome in repeated
    )
    assert newer.status is EndToEndStatus.PROCESSED and newer.raw_item_count == 1
    assert older.status is EndToEndStatus.COLLECTION_FAILED and older.raw_item_count == 0
    async with factory() as session:
        raw_count = int((await session.scalar(select(func.count()).select_from(RawItem))) or 0)
        evidence_count = int(
            (await session.scalar(select(func.count()).select_from(EvidenceItem))) or 0
        )
        content_count = int(
            (await session.scalar(select(func.count()).select_from(ContentItem))) or 0
        )
        cursor = await session.scalar(
            select(CollectionCursor).where(
                CollectionCursor.source_account_id == target.source_account_id
            )
        )
        runs = (
            await session.scalars(select(CollectionRun).order_by(CollectionRun.started_at))
        ).all()
    assert (raw_count, evidence_count, content_count) == (2, 2, 2)
    assert cursor is not None and filing_b in (cursor.cursor_value or "")
    assert cursor.last_published_at == datetime(2026, 8, 2, tzinfo=UTC)
    assert all(run.status is CollectionRunStatus.SUCCEEDED for run in runs[:5])
    assert all(run.error_count == 0 and run.error_code is None for run in runs[:5])
    assert runs[-1].status is CollectionRunStatus.FAILED
    assert runs[-1].error_code == "COLLECTION_CONTRACT_INVALID"
    assert len(transport.calls) == 6


@pytest.mark.asyncio
async def test_eia_snapshot_polling_initial_same_newer_and_older(provider_runtime) -> None:
    factory, redis = provider_runtime
    target = await _target(factory, "eia", {"dataset": "electricity"})
    transport = MockProviderTransport(
        [
            _eia_response("2026-05"),
            _eia_response("2026-05"),
            _eia_response("2026-05"),
            _eia_response("2026-05"),
            _eia_response("2026-06"),
            _eia_response("2026-04"),
        ]
    )
    pipeline = MultiProviderIngestionPipeline(
        factory, redis, _settings(1), _adapter("eia"), transport
    )

    initial = await pipeline.run(target)
    repeated = [await pipeline.run(target) for _ in range(3)]
    newer = await pipeline.run(target)
    older = await pipeline.run(target)

    assert initial.status is EndToEndStatus.PROCESSED and initial.raw_item_count == 1
    assert all(
        outcome.status is EndToEndStatus.PROCESSED
        and outcome.raw_item_count == 0
        and outcome.trigger_outcomes == ()
        and outcome.safe_errors == ()
        for outcome in repeated
    )
    assert newer.status is EndToEndStatus.PROCESSED and newer.raw_item_count == 1
    assert older.status is EndToEndStatus.COLLECTION_FAILED and older.raw_item_count == 0
    async with factory() as session:
        raw_count = int((await session.scalar(select(func.count()).select_from(RawItem))) or 0)
        evidence_count = int(
            (await session.scalar(select(func.count()).select_from(EvidenceItem))) or 0
        )
        content_count = int(
            (await session.scalar(select(func.count()).select_from(ContentItem))) or 0
        )
        cursor = await session.scalar(
            select(CollectionCursor).where(
                CollectionCursor.source_account_id == target.source_account_id
            )
        )
        runs = (
            await session.scalars(select(CollectionRun).order_by(CollectionRun.started_at))
        ).all()
    assert (raw_count, evidence_count, content_count) == (2, 2, 2)
    assert cursor is not None and "2026-06:AK:ALL" in (cursor.cursor_value or "")
    assert cursor.last_published_at == datetime(2026, 6, 1, tzinfo=UTC)
    assert all(run.status is CollectionRunStatus.SUCCEEDED for run in runs[:5])
    assert all(run.error_count == 0 and run.error_code is None for run in runs[:5])
    assert runs[-1].status is CollectionRunStatus.FAILED
    assert runs[-1].error_code == "COLLECTION_CONTRACT_INVALID"
    assert len(transport.calls) == 6


@pytest.mark.parametrize(
    ("provider", "options"),
    [
        ("eia", {"dataset": "electricity"}),
        ("sec_edgar", {"ticker": "AAPL", "cik": "0000320193"}),
    ],
)
@pytest.mark.asyncio
async def test_bounded_pipeline_ignores_has_more_after_one_request(
    provider_runtime, provider, options
) -> None:
    factory, redis = provider_runtime
    target = await _target(factory, provider, options)
    transport = MockProviderTransport([_multi_row_response(provider)])
    pipeline = MultiProviderIngestionPipeline(
        factory,
        redis,
        _settings(1),
        _adapter(provider),
        transport,
        max_batches=1,
    )

    outcome = await pipeline.run(target)

    assert outcome.status is EndToEndStatus.PROCESSED
    assert outcome.raw_item_count == 1
    assert len(transport.calls) == 1


@pytest.mark.asyncio
async def test_normal_pipeline_continues_when_provider_has_more(provider_runtime) -> None:
    factory, redis = provider_runtime
    target = await _target(factory, "eia", {"dataset": "electricity"})
    first = ProviderTransportResponse(
        200,
        datetime.now(UTC),
        {
            "response": {
                "data": [
                    {"period": "2026-07", "price": 1, "stateid": "US", "sectorid": "ALL"},
                    {"period": "2026-06", "price": 2, "stateid": "US", "sectorid": "ALL"},
                ]
            }
        },
    )
    second = ProviderTransportResponse(
        200,
        datetime.now(UTC),
        {
            "response": {
                "data": [{"period": "2026-08", "price": 3, "stateid": "US", "sectorid": "ALL"}]
            }
        },
    )
    transport = MockProviderTransport([first, second])
    pipeline = MultiProviderIngestionPipeline(
        factory, redis, _settings(1), _adapter("eia"), transport
    )

    outcome = await pipeline.run(target)

    assert outcome.status is EndToEndStatus.PROCESSED
    assert outcome.raw_item_count == 2
    assert len(transport.calls) == 2


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


@pytest.mark.parametrize(
    ("provider", "expected_options"),
    [
        ("finnhub", {"symbol": "AAPL"}),
        ("eia", {"dataset": "electricity"}),
        ("sec_edgar", {"ticker": "AAPL", "cik": "0000320193"}),
    ],
)
@pytest.mark.asyncio
async def test_provider_target_doctor_and_bootstrap_are_idempotent(
    provider_runtime, provider, expected_options
) -> None:
    factory, _ = provider_runtime

    missing = await diagnose_provider_target(factory, provider)
    first = await bootstrap_provider_target(factory, provider)
    second = await bootstrap_provider_target(factory, provider)
    ready = await diagnose_provider_target(factory, provider)

    assert missing.error is ProviderTargetError.MISSING
    assert target_summary("BLOCKED", missing)["safe_errors"] == ["provider_target_missing"]
    assert first.status == "created"
    assert second.status == "already_exists"
    assert ready.target is not None
    assert ready.eligible_target_count == 1
    assert target_summary("PASS", ready)["status"] == "PASS"
    async with factory() as session:
        account = await session.scalar(
            select(SourceAccount)
            .join(Source, Source.id == SourceAccount.source_id)
            .where(Source.access_method == provider)
        )
    assert account is not None
    assert account.collection_options == expected_options
    rendered = repr(account.collection_options).lower()
    assert all(marker not in rendered for marker in (SECRET, "api_key", "token", "contact"))


@pytest.mark.asyncio
async def test_conflicting_provider_targets_fail_closed(provider_runtime) -> None:
    factory, _ = provider_runtime
    await _target(factory, "finnhub", {"symbol": "AAPL"})
    await _target(factory, "finnhub", {"symbol": "MSFT"})

    diagnosis = await diagnose_provider_target(factory, "finnhub")
    bootstrap = await bootstrap_provider_target(factory, "finnhub")

    assert diagnosis.error is ProviderTargetError.NOT_UNIQUE
    assert diagnosis.eligible_target_count == 2
    assert bootstrap.status == "blocked"


@pytest.mark.parametrize(
    ("enabled", "authorization", "expected"),
    [
        (False, AuthorizationStatus.AUTHORIZED, ProviderTargetError.SOURCE_DISABLED),
        (True, AuthorizationStatus.PLANNED, ProviderTargetError.SOURCE_UNAUTHORIZED),
    ],
)
@pytest.mark.asyncio
async def test_provider_doctor_distinguishes_source_gate_failures(
    provider_runtime, enabled, authorization, expected
) -> None:
    factory, _ = provider_runtime
    async with factory.begin() as session:
        session.add(
            Source(
                code=f"gate-{uuid.uuid4().hex}",
                name="Synthetic gate",
                source_type=SourceType.API,
                access_method="finnhub",
                authorization_status=authorization,
                retention_class="metadata_only",
                enabled=enabled,
            )
        )

    diagnosis = await diagnose_provider_target(factory, "finnhub")

    assert diagnosis.error is expected


@pytest.mark.asyncio
async def test_provider_doctor_distinguishes_missing_account(provider_runtime) -> None:
    factory, _ = provider_runtime
    async with factory.begin() as session:
        session.add(
            Source(
                code=f"account-{uuid.uuid4().hex}",
                name="Synthetic account gate",
                source_type=SourceType.API,
                access_method="eia",
                authorization_status=AuthorizationStatus.AUTHORIZED,
                retention_class="metadata_only",
                enabled=True,
            )
        )

    diagnosis = await diagnose_provider_target(factory, "eia")

    assert diagnosis.error is ProviderTargetError.ACCOUNT_MISSING


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "environ",
    [
        {"SEC_USER_AGENT": "synthetic-agent"},
        {"SEC_CONTACT_EMAIL": "synthetic@example.invalid"},
        {},
    ],
)
async def test_sec_runtime_requires_agent_and_contact(environ) -> None:
    report, code = await execute_provider(
        "sec_edgar",
        1,
        environ,
        {"ticker": "AAPL", "cik": "0000320193"},
        MockProviderTransport([]),
    )
    assert code == 2
    assert report["status"] == "BLOCKED"
    assert report["safe_errors"] == ["provider_runtime_credential_missing"]
    rendered = json.dumps(report).lower()
    assert "synthetic-agent" not in rendered
    assert "synthetic@example.invalid" not in rendered


@pytest.mark.asyncio
async def test_sec_transport_constructs_user_agent_without_echo() -> None:
    observed: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed["user-agent"] = request.headers["User-Agent"]
        return httpx.Response(200, json={"filings": {"recent": {}}})

    credential = RuntimeCredential("SEC_USER_AGENT", "synthetic-agent synthetic@example.invalid")
    transport = HttpxProviderTransport(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    request = ProviderTransportRequest(
        provider="sec_edgar",
        operation="submissions",
        params={"cik": "0000320193"},
        timeout_seconds=1,
        runtime_credential=credential,
    )

    response = await transport.send(request)

    assert response.status_code == 200
    assert observed["user-agent"] == "synthetic-agent synthetic@example.invalid"
    rendered = f"{request!r} {response!r}"
    assert "synthetic-agent" not in rendered
    assert "synthetic@example.invalid" not in rendered
