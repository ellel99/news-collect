import importlib.util
import inspect
import json
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from redis.asyncio import Redis
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from market_intelligence.collection.contracts import CollectionTarget
from market_intelligence.core.config import Settings
from market_intelligence.db import Base, EvidenceItem, RawItem
from market_intelligence.db.models import AuthorizationStatus, Source, SourceAccount, SourceType
from market_intelligence.evidence.end_to_end import EndToEndStatus
from market_intelligence.evidence.orchestration import EvidencePipelineStatus
from market_intelligence.pipeline.marketaux_real_collection import (
    MarketauxRealCollectionPipeline,
    MarketauxTargetError,
    bootstrap_marketaux_target,
    diagnose_marketaux_target,
)
from market_intelligence.providers.contracts import (
    ProviderTransportResponse,
    ProviderTransportTimeout,
)
from market_intelligence.providers.credentials import RuntimeCredential
from market_intelligence.providers.transport import MockProviderTransport

_SCRIPT_SPEC = importlib.util.spec_from_file_location(
    "marketaux_real_collection_smoke",
    Path(__file__).parents[1] / "scripts" / "marketaux_real_collection_smoke.py",
)
assert _SCRIPT_SPEC is not None and _SCRIPT_SPEC.loader is not None
marketaux_real_collection_smoke = importlib.util.module_from_spec(_SCRIPT_SPEC)
_SCRIPT_SPEC.loader.exec_module(marketaux_real_collection_smoke)

POSTGRES_TEST_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://market_intelligence:local_dev_only@localhost:5432/market_intelligence",
)
REDIS_TEST_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/13")
SECRET = "synthetic-runtime-secret-never-print"


@pytest_asyncio.fixture
async def real_pipeline_runtime() -> AsyncIterator[tuple[async_sessionmaker[AsyncSession], Redis]]:
    schema = f"spec_0032_real_pipeline_{uuid.uuid4().hex}"
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
) -> tuple[Source, SourceAccount]:
    async with factory.begin() as session:
        source = Source(
            code=f"spec0032-{uuid.uuid4().hex}",
            name="Synthetic Marketaux Runtime",
            source_type=SourceType.API,
            access_method="marketaux",
            authorization_status=AuthorizationStatus.AUTHORIZED,
            retention_class="metadata_only",
            enabled=True,
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


def _target(source: Source, account: SourceAccount) -> CollectionTarget:
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


def _response(*, status: int = 200, item_id: str = "synthetic-real-runtime-item"):
    return ProviderTransportResponse(
        status_code=status,
        received_at=datetime.now(UTC),
        body={
            "data": [
                {
                    "uuid": item_id,
                    "published_at": "2026-08-04T10:00:00Z",
                    "title": "must not reach outcome",
                    "description": "must not reach outcome",
                    "snippet": "must not reach outcome",
                    "url": "https://example.invalid/must-not-reach-outcome",
                }
            ]
        },
    )


def _pipeline(factory, redis, response):
    transport = MockProviderTransport([response])
    pipeline = MarketauxRealCollectionPipeline(
        factory,
        redis,
        _settings(),
        RuntimeCredential("MARKETAUX_API_TOKEN", SECRET),
        transport,
    )
    return pipeline, transport


async def _counts(factory) -> tuple[int, int]:
    async with factory() as session:
        raw_count = int((await session.scalar(select(func.count()).select_from(RawItem))) or 0)
        evidence_count = int(
            (await session.scalar(select(func.count()).select_from(EvidenceItem))) or 0
        )
        return raw_count, evidence_count


def test_default_dry_run_has_safe_inert_summary(capsys) -> None:
    exit_code = marketaux_real_collection_smoke.main([])
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert report == {
        "collection_status": "not_started",
        "cursor_present": False,
        "db_written": False,
        "evidence_item_count": 0,
        "provider": "marketaux",
        "raw_item_count": 0,
        "response_saved": False,
        "safe_errors": [],
        "status": "DRY_RUN",
        "token_read": False,
    }


def test_default_dry_run_does_not_open_runtime(monkeypatch, capsys) -> None:
    def fail(*args, **kwargs):
        del args, kwargs
        raise AssertionError("dry-run opened a runtime boundary")

    monkeypatch.setattr(marketaux_real_collection_smoke, "create_engine", fail)
    monkeypatch.setattr(marketaux_real_collection_smoke.Redis, "from_url", fail)
    exit_code = marketaux_real_collection_smoke.main([])

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["status"] == "DRY_RUN"


@pytest.mark.asyncio
async def test_doctor_on_empty_database_fails_closed(real_pipeline_runtime) -> None:
    factory, _ = real_pipeline_runtime

    diagnosis = await diagnose_marketaux_target(factory)

    assert diagnosis.source_count == 0
    assert diagnosis.account_count == 0
    assert diagnosis.eligible_target_count == 0
    assert diagnosis.error is MarketauxTargetError.MISSING


@pytest.mark.asyncio
async def test_bootstrap_creates_target_and_is_idempotent(real_pipeline_runtime) -> None:
    factory, _ = real_pipeline_runtime

    first = await bootstrap_marketaux_target(factory)
    second = await bootstrap_marketaux_target(factory)

    assert first.status == "created"
    assert first.diagnosis.target is not None
    assert first.diagnosis.target.target.retention_class == "metadata_only"
    assert first.diagnosis.target.target.collection_options == {"query": "technology"}
    assert second.status == "already_exists"
    assert second.diagnosis.source_count == 1
    assert second.diagnosis.account_count == 1
    assert second.diagnosis.eligible_target_count == 1


@pytest.mark.asyncio
async def test_bootstrap_fails_closed_for_multiple_targets(real_pipeline_runtime) -> None:
    factory, _ = real_pipeline_runtime
    await _source(factory)
    await _source(factory)

    result = await bootstrap_marketaux_target(factory)

    assert result.status == "blocked"
    assert result.diagnosis.error is MarketauxTargetError.NOT_UNIQUE
    assert result.diagnosis.eligible_target_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("enabled", "authorization", "account_enabled", "expected"),
    [
        (False, AuthorizationStatus.AUTHORIZED, True, MarketauxTargetError.DISABLED),
        (True, AuthorizationStatus.PLANNED, True, MarketauxTargetError.UNAUTHORIZED),
        (True, AuthorizationStatus.AUTHORIZED, False, MarketauxTargetError.DISABLED),
    ],
)
async def test_doctor_distinguishes_ineligible_targets(
    real_pipeline_runtime, enabled, authorization, account_enabled, expected
) -> None:
    factory, _ = real_pipeline_runtime
    source, account = await _source(factory)
    async with factory.begin() as session:
        stored_source = await session.get(Source, source.id)
        stored_account = await session.get(SourceAccount, account.id)
        assert stored_source is not None and stored_account is not None
        stored_source.enabled = enabled
        stored_source.authorization_status = authorization
        stored_account.enabled = account_enabled

    diagnosis = await diagnose_marketaux_target(factory)

    assert diagnosis.error is expected
    assert diagnosis.eligible_target_count == 0


@pytest.mark.asyncio
async def test_doctor_distinguishes_missing_account(real_pipeline_runtime) -> None:
    factory, _ = real_pipeline_runtime
    async with factory.begin() as session:
        session.add(
            Source(
                code=f"spec0032-no-account-{uuid.uuid4().hex}",
                name="Synthetic Marketaux Missing Account",
                source_type=SourceType.API,
                access_method="marketaux",
                authorization_status=AuthorizationStatus.AUTHORIZED,
                retention_class="metadata_only",
                enabled=True,
                schedule_seconds=None,
            )
        )

    diagnosis = await diagnose_marketaux_target(factory)

    assert diagnosis.error is MarketauxTargetError.ACCOUNT_MISSING
    assert diagnosis.account_count == 0


@pytest.mark.asyncio
async def test_bootstrapped_target_reaches_raw_and_evidence(real_pipeline_runtime) -> None:
    factory, redis = real_pipeline_runtime
    bootstrap = await bootstrap_marketaux_target(factory)
    assert bootstrap.diagnosis.target is not None
    pipeline, transport = _pipeline(factory, redis, _response())

    outcome = await pipeline.run(bootstrap.diagnosis.target.target)

    assert outcome.status is EndToEndStatus.PROCESSED
    assert await _counts(factory) == (1, 1)
    assert len(transport.calls) == 1


@pytest.mark.asyncio
async def test_bootstrap_does_not_read_token_or_request_api(
    real_pipeline_runtime, monkeypatch
) -> None:
    factory, _ = real_pipeline_runtime

    def fail(*args, **kwargs):
        del args, kwargs
        raise AssertionError("bootstrap crossed credential or network boundary")

    monkeypatch.setattr(marketaux_real_collection_smoke.os.environ, "get", fail)
    result = await bootstrap_marketaux_target(factory)

    assert result.status == "created"


@pytest.mark.asyncio
async def test_execute_missing_token_fails_before_runtime() -> None:
    report, exit_code = await marketaux_real_collection_smoke.execute_collection(
        limit=1, environ={}
    )
    assert exit_code == 2
    assert report["token_read"] is False
    assert report["db_written"] is False
    assert report["safe_errors"] == ["provider_runtime_credential_missing"]


def test_limit_above_three_fails_without_runtime(capsys) -> None:
    exit_code = marketaux_real_collection_smoke.main(["--execute", "--limit", "4"])
    report = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert report["token_read"] is False
    assert report["safe_errors"] == ["provider_record_limit_invalid"]


def test_collection_contract_failure_diagnostics_are_specific_and_safe() -> None:
    errors = marketaux_real_collection_smoke._safe_collection_errors(
        ["collection_not_succeeded"],
        "COLLECTION_CONTRACT_INVALID",
        "provider_request_rejected",
    )
    report = marketaux_real_collection_smoke._summary(
        status="FAIL",
        collection_status="collection_failed",
        raw_item_count=0,
        evidence_item_count=0,
        cursor_present=True,
        safe_errors=errors,
        db_written=False,
        token_read=True,
        collection_run_id_present=True,
        collection_run_status="failed",
        collection_error_code="COLLECTION_CONTRACT_INVALID",
        content_item_count=0,
    )

    assert "collection_contract_invalid" in report["safe_errors"]
    assert "provider_request_rejected" in report["safe_errors"]
    assert report["collection_run_id_present"] is True
    assert report["collection_run_status"] == "failed"
    assert report["collection_error_code"] == "COLLECTION_CONTRACT_INVALID"
    assert report["content_item_count"] == 0
    rendered = repr(report).lower()
    assert all(
        term not in rendered
        for term in (SECRET.lower(), "authorization", "https://", "title", "snippet")
    )


@pytest.mark.asyncio
async def test_mocked_real_adapter_collection_reaches_evidence(
    real_pipeline_runtime,
) -> None:
    factory, redis = real_pipeline_runtime
    source, account = await _source(factory)
    pipeline, transport = _pipeline(factory, redis, _response())

    outcome = await pipeline.run(_target(source, account))

    assert outcome.status is EndToEndStatus.PROCESSED
    assert outcome.raw_item_count == 1
    assert outcome.trigger_outcomes[0].pipeline_outcome is not None
    assert outcome.trigger_outcomes[0].pipeline_outcome.status is EvidencePipelineStatus.WRITTEN
    assert await _counts(factory) == (1, 1)
    assert len(transport.calls) == 1
    rendered = repr(outcome).lower()
    assert all(
        value not in rendered
        for value in (
            SECRET,
            "must not reach outcome",
            "example.invalid",
            "title",
            "body",
            "url",
            "snippet",
            "description",
            "raw response",
        )
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [_response(status=429), ProviderTransportTimeout()])
async def test_provider_failure_writes_no_raw_or_evidence(real_pipeline_runtime, response) -> None:
    factory, redis = real_pipeline_runtime
    source, account = await _source(factory)
    pipeline, _ = _pipeline(factory, redis, response)

    outcome = await pipeline.run(_target(source, account))

    assert outcome.status is EndToEndStatus.COLLECTION_FAILED
    assert await _counts(factory) == (0, 0)


@pytest.mark.asyncio
async def test_raw_persistence_failure_writes_no_evidence(real_pipeline_runtime) -> None:
    factory, redis = real_pipeline_runtime
    source, account = await _source(factory)
    pipeline, _ = _pipeline(factory, redis, _response(item_id="x" * 300))

    outcome = await pipeline.run(_target(source, account))

    assert outcome.status is EndToEndStatus.COLLECTION_FAILED
    assert await _counts(factory) == (0, 0)


@pytest.mark.asyncio
async def test_duplicate_processing_does_not_duplicate_evidence(
    real_pipeline_runtime,
) -> None:
    factory, redis = real_pipeline_runtime
    source, account = await _source(factory)
    pipeline, _ = _pipeline(factory, redis, _response())
    first = await pipeline.run(_target(source, account))
    assert first.collection_run_id is not None

    second = await pipeline.process_run(first.collection_run_id)

    assert second.trigger_outcomes[0].pipeline_outcome is not None
    assert second.trigger_outcomes[0].pipeline_outcome.status is EvidencePipelineStatus.DUPLICATE
    assert await _counts(factory) == (1, 1)


def test_runtime_source_has_no_forbidden_dependencies() -> None:
    import market_intelligence.pipeline.marketaux_real_collection as pipeline_module
    import market_intelligence.providers.runtime as runtime_module

    source = "\n".join(
        (
            inspect.getsource(pipeline_module),
            inspect.getsource(runtime_module),
            inspect.getsource(marketaux_real_collection_smoke),
        )
    ).lower()
    forbidden = (
        "dotenv",
        "local_evaluation",
        "provider_capture",
        "import scheduler",
        "import telegram",
        "import openai",
        "import recommendation",
        "import dedup",
        "import event",
    )
    assert all(term not in source for term in forbidden)
    assert "_env_file=none" in source.replace(" ", "").replace("\n", "")
