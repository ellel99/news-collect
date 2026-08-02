import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from market_intelligence.db import Base, EvidenceItem
from market_intelligence.evidence.orchestration import (
    EvidencePipelineRequest,
    EvidencePipelineService,
    EvidencePipelineStatus,
)
from market_intelligence.evidence.write_path import EvidenceWriteService

POSTGRES_TEST_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://market_intelligence:local_dev_only@localhost:5432/market_intelligence",
)


@pytest_asyncio.fixture
async def pipeline_session() -> AsyncIterator[AsyncSession]:
    schema = f"spec_0027_pipeline_{uuid.uuid4().hex}"
    engine = create_async_engine(
        POSTGRES_TEST_URL,
        connect_args={"server_settings": {"search_path": schema}},
    )
    async with engine.connect() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        await connection.commit()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            yield session
    finally:
        async with engine.connect() as connection:
            await connection.rollback()
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            await connection.commit()
        await engine.dispose()


async def _raw_provenance(session: AsyncSession) -> dict[str, uuid.UUID]:
    now = datetime.now(UTC)
    source_id = (
        await session.execute(
            text(
                """
                INSERT INTO sources (
                    code, name, source_type, access_method, authorization_status,
                    retention_class, enabled
                ) VALUES (
                    :code, 'Pipeline Fixture', 'api', 'marketaux', 'authorized',
                    'metadata_only', true
                ) RETURNING id
                """
            ),
            {"code": f"pipeline-{uuid.uuid4().hex}"},
        )
    ).scalar_one()
    account_id = (
        await session.execute(
            text(
                """
                INSERT INTO source_accounts (
                    source_id, identity_status, enabled, collection_options
                ) VALUES (:source_id, 'verified', true, '{}'::jsonb)
                RETURNING id
                """
            ),
            {"source_id": source_id},
        )
    ).scalar_one()
    run_id = (
        await session.execute(
            text(
                """
                INSERT INTO collection_runs (
                    source_id, source_account_id, started_at, finished_at, status
                ) VALUES (:source_id, :account_id, :now, :now, 'succeeded')
                RETURNING id
                """
            ),
            {"source_id": source_id, "account_id": account_id, "now": now},
        )
    ).scalar_one()
    raw_item_id = (
        await session.execute(
            text(
                """
                INSERT INTO raw_items (
                    source_id, source_account_id, collection_run_id, external_id,
                    fetched_at, payload_location, payload_hash, retention_class, parse_status
                ) VALUES (
                    :source_id, :account_id, :run_id, 'synthetic-marketaux-item',
                    :now, :reference, :payload_hash, 'metadata_only', 'pending'
                ) RETURNING id
                """
            ),
            {
                "source_id": source_id,
                "account_id": account_id,
                "run_id": run_id,
                "now": now,
                "reference": "internal://provider/marketaux/synthetic",
                "payload_hash": "a" * 64,
            },
        )
    ).scalar_one()
    await session.flush()
    return {
        "source_id": source_id,
        "source_account_id": account_id,
        "raw_item_id": raw_item_id,
    }


def _projection() -> dict[str, object]:
    return {
        "provider_item_id": "synthetic-marketaux-item",
        "published_at": "2026-08-02T10:00:00Z",
        "field_names": ("published_at", "title", "url", "uuid"),
        "has_title": True,
        "has_description": False,
        "has_snippet": False,
        "has_source_url": True,
        "payload_hash": "a" * 64,
        "payload_reference": "internal://provider/marketaux/synthetic",
    }


def _request(
    provenance: dict[str, uuid.UUID],
    *,
    provider: str = "marketaux",
    projection: dict[str, object] | None = None,
    raw_item_id: uuid.UUID | None = None,
    source_id: uuid.UUID | None = None,
) -> EvidencePipelineRequest:
    return EvidencePipelineRequest(
        raw_item_id=raw_item_id or provenance["raw_item_id"],
        source_id=source_id or provenance["source_id"],
        source_account_id=provenance["source_account_id"],
        provider=provider,
        sanitized_projection=projection or _projection(),
        observed_at=datetime(2026, 8, 2, 10, 1, tzinfo=UTC),
        correlation_id="synthetic-correlation",
    )


async def _count_evidence(session: AsyncSession) -> int:
    return int((await session.scalar(select(func.count()).select_from(EvidenceItem))) or 0)


@pytest.mark.asyncio
async def test_marketaux_projection_writes_evidence_through_write_service(
    pipeline_session: AsyncSession,
) -> None:
    provenance = await _raw_provenance(pipeline_session)
    service = EvidencePipelineService(EvidenceWriteService(pipeline_session))

    outcome = await service.process(_request(provenance))

    assert outcome.status is EvidencePipelineStatus.WRITTEN
    assert outcome.evidence_item_id is not None
    assert outcome.provider_item_hash == "a" * 64
    assert await _count_evidence(pipeline_session) == 1


@pytest.mark.asyncio
async def test_duplicate_projection_returns_duplicate(
    pipeline_session: AsyncSession,
) -> None:
    provenance = await _raw_provenance(pipeline_session)
    service = EvidencePipelineService(EvidenceWriteService(pipeline_session))
    request = _request(provenance)

    first = await service.process(request)
    second = await service.process(request)

    assert first.status is EvidencePipelineStatus.WRITTEN
    assert second.status is EvidencePipelineStatus.DUPLICATE
    assert second.evidence_item_id == first.evidence_item_id
    assert await _count_evidence(pipeline_session) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("request_change", "error_code"),
    [
        ({"provider": "unknown"}, "provider_unsupported"),
        ({"projection": {"unexpected": "unsafe"}}, "projection_invalid"),
    ],
)
async def test_unsupported_or_malformed_projection_fails_closed(
    pipeline_session: AsyncSession,
    request_change: dict[str, object],
    error_code: str,
) -> None:
    provenance = await _raw_provenance(pipeline_session)
    outcome = await EvidencePipelineService(EvidenceWriteService(pipeline_session)).process(
        _request(provenance, **request_change)  # type: ignore[arg-type]
    )

    assert outcome.status in {EvidencePipelineStatus.SKIPPED, EvidencePipelineStatus.INVALID}
    assert outcome.safe_errors[0].code == error_code
    assert await _count_evidence(pipeline_session) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("request_change", "error_code"),
    [
        ({"raw_item_id": uuid.uuid4()}, "reference_not_found"),
        ({"source_id": uuid.uuid4()}, "provenance_mismatch"),
    ],
)
async def test_missing_or_mismatched_raw_provenance_fails_closed(
    pipeline_session: AsyncSession,
    request_change: dict[str, uuid.UUID],
    error_code: str,
) -> None:
    provenance = await _raw_provenance(pipeline_session)
    outcome = await EvidencePipelineService(EvidenceWriteService(pipeline_session)).process(
        _request(provenance, **request_change)
    )

    assert outcome.status is EvidencePipelineStatus.INVALID
    assert outcome.safe_errors[0].code == error_code
    assert await _count_evidence(pipeline_session) == 0


@pytest.mark.asyncio
async def test_unsafe_projection_never_reaches_safe_outcome(
    pipeline_session: AsyncSession,
) -> None:
    secret = "never-echo-secret-value"
    provenance = await _raw_provenance(pipeline_session)
    projection = _projection()
    projection["payload_reference"] = f"internal://api_key={secret}"

    outcome = await EvidencePipelineService(EvidenceWriteService(pipeline_session)).process(
        _request(provenance, projection=projection)
    )

    assert outcome.status is EvidencePipelineStatus.INVALID
    assert secret not in repr(outcome)
    assert await _count_evidence(pipeline_session) == 0


def test_pipeline_source_has_no_forbidden_runtime_dependencies() -> None:
    path = (
        Path(__file__).parents[1] / "src" / "market_intelligence" / "evidence" / "orchestration.py"
    )
    source = path.read_text()
    forbidden = (
        "import requests",
        "import httpx",
        "provider_capture",
        "local_evaluation",
        "collection.runner",
        "scheduler",
        "OpenAI",
        "Telegram",
        "Recommendation",
        "sqlalchemy",
        "EvidenceItem",
    )
    assert all(marker not in source for marker in forbidden)
