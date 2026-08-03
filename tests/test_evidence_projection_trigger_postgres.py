import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from market_intelligence.db import Base, EvidenceItem
from market_intelligence.evidence.orchestration import (
    EvidencePipelineService,
    EvidencePipelineStatus,
)
from market_intelligence.evidence.pipeline_trigger import (
    EvidenceTriggerStatus,
    RawItemEvidencePipelineTrigger,
)
from market_intelligence.evidence.projection_store import (
    InMemoryEvidenceProjectionStore,
    RawItemEvidenceProjection,
    SqlAlchemyRawItemProjectionReader,
)
from market_intelligence.evidence.write_path import EvidenceWriteService

POSTGRES_TEST_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://market_intelligence:local_dev_only@localhost:5432/market_intelligence",
)


@pytest_asyncio.fixture
async def trigger_session() -> AsyncIterator[AsyncSession]:
    schema = f"spec_0028_trigger_{uuid.uuid4().hex}"
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


async def _raw_item(session: AsyncSession) -> dict[str, object]:
    now = datetime.now(UTC)
    source_id = (
        await session.execute(
            text(
                """
                INSERT INTO sources (
                    code, name, source_type, access_method, authorization_status,
                    retention_class, enabled
                ) VALUES (
                    :code, 'Trigger Fixture', 'api', 'marketaux', 'authorized',
                    'metadata_only', true
                ) RETURNING id
                """
            ),
            {"code": f"trigger-{uuid.uuid4().hex}"},
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
    external_id = "synthetic-trigger-item"
    payload_hash = "b" * 64
    payload_reference = "internal://provider/marketaux/trigger"
    raw_item_id = (
        await session.execute(
            text(
                """
                INSERT INTO raw_items (
                    source_id, source_account_id, collection_run_id, external_id,
                    fetched_at, payload_location, payload_hash, retention_class, parse_status
                ) VALUES (
                    :source_id, :account_id, :run_id, :external_id, :now,
                    :payload_reference, :payload_hash, 'metadata_only', 'pending'
                ) RETURNING id
                """
            ),
            {
                "source_id": source_id,
                "account_id": account_id,
                "run_id": run_id,
                "external_id": external_id,
                "now": now,
                "payload_reference": payload_reference,
                "payload_hash": payload_hash,
            },
        )
    ).scalar_one()
    await session.flush()
    return {
        "raw_item_id": raw_item_id,
        "source_id": source_id,
        "source_account_id": account_id,
        "external_id": external_id,
        "payload_hash": payload_hash,
        "payload_reference": payload_reference,
        "observed_at": now,
    }


def _projection(raw: dict[str, object]) -> RawItemEvidenceProjection:
    return RawItemEvidenceProjection(
        raw_item_id=raw["raw_item_id"],  # type: ignore[arg-type]
        source_id=raw["source_id"],  # type: ignore[arg-type]
        source_account_id=raw["source_account_id"],  # type: ignore[arg-type]
        provider="marketaux",
        sanitized_projection={
            "provider_item_id": raw["external_id"],
            "published_at": "2026-08-03T09:00:00Z",
            "field_names": ("published_at", "title", "url", "uuid"),
            "has_title": True,
            "has_description": False,
            "has_snippet": False,
            "has_source_url": True,
            "payload_hash": raw["payload_hash"],
            "payload_reference": raw["payload_reference"],
        },
        observed_at=raw["observed_at"],  # type: ignore[arg-type]
        correlation_id="synthetic-trigger-correlation",
    )


def _trigger(
    session: AsyncSession,
    store: InMemoryEvidenceProjectionStore,
) -> RawItemEvidencePipelineTrigger:
    return RawItemEvidencePipelineTrigger(
        SqlAlchemyRawItemProjectionReader(session),
        store,
        EvidencePipelineService(EvidenceWriteService(session)),
    )


async def _evidence_count(session: AsyncSession) -> int:
    return int((await session.scalar(select(func.count()).select_from(EvidenceItem))) or 0)


@pytest.mark.asyncio
async def test_safe_projection_trigger_writes_evidence(
    trigger_session: AsyncSession,
) -> None:
    raw = await _raw_item(trigger_session)
    store = InMemoryEvidenceProjectionStore()
    store.save(_projection(raw))

    outcome = await _trigger(trigger_session, store).trigger(raw["raw_item_id"])  # type: ignore[arg-type]

    assert outcome.status is EvidenceTriggerStatus.PROCESSED
    assert outcome.pipeline_outcome is not None
    assert outcome.pipeline_outcome.status is EvidencePipelineStatus.WRITTEN
    assert await _evidence_count(trigger_session) == 1


@pytest.mark.asyncio
async def test_duplicate_trigger_does_not_duplicate_evidence(
    trigger_session: AsyncSession,
) -> None:
    raw = await _raw_item(trigger_session)
    store = InMemoryEvidenceProjectionStore()
    store.save(_projection(raw))
    trigger = _trigger(trigger_session, store)

    first = await trigger.trigger(raw["raw_item_id"])  # type: ignore[arg-type]
    second = await trigger.trigger(raw["raw_item_id"])  # type: ignore[arg-type]

    assert first.pipeline_outcome is not None
    assert first.pipeline_outcome.status is EvidencePipelineStatus.WRITTEN
    assert second.pipeline_outcome is not None
    assert second.pipeline_outcome.status is EvidencePipelineStatus.DUPLICATE
    assert await _evidence_count(trigger_session) == 1


@pytest.mark.asyncio
async def test_missing_raw_item_fails_closed(trigger_session: AsyncSession) -> None:
    missing = uuid.uuid4()
    outcome = await _trigger(trigger_session, InMemoryEvidenceProjectionStore()).trigger(missing)

    assert outcome.status is EvidenceTriggerStatus.INVALID
    assert outcome.safe_errors[0].code == "raw_item_not_found"
    assert await _evidence_count(trigger_session) == 0


@pytest.mark.asyncio
async def test_missing_projection_fails_closed(trigger_session: AsyncSession) -> None:
    raw = await _raw_item(trigger_session)
    outcome = await _trigger(trigger_session, InMemoryEvidenceProjectionStore()).trigger(
        raw["raw_item_id"]
    )  # type: ignore[arg-type]

    assert outcome.status is EvidenceTriggerStatus.SKIPPED
    assert outcome.safe_errors[0].code == "projection_not_found"
    assert await _evidence_count(trigger_session) == 0


@pytest.mark.asyncio
async def test_projection_provenance_mismatch_fails_closed(
    trigger_session: AsyncSession,
) -> None:
    raw = await _raw_item(trigger_session)
    projection = replace(_projection(raw), source_id=uuid.uuid4())
    store = InMemoryEvidenceProjectionStore()
    store.save(projection)

    outcome = await _trigger(trigger_session, store).trigger(raw["raw_item_id"])  # type: ignore[arg-type]

    assert outcome.status is EvidenceTriggerStatus.INVALID
    assert outcome.safe_errors[0].code == "projection_mismatch"
    assert await _evidence_count(trigger_session) == 0


def test_store_rejects_content_or_secret_without_retaining_it() -> None:
    raw = {
        "raw_item_id": uuid.uuid4(),
        "source_id": uuid.uuid4(),
        "source_account_id": uuid.uuid4(),
        "external_id": "synthetic-trigger-item",
        "payload_hash": "b" * 64,
        "payload_reference": "internal://provider/marketaux/trigger",
        "observed_at": datetime.now(UTC),
    }
    secret = "never-retain-this-secret"
    base_projection = _projection(raw)
    content_values = dict(base_projection.sanitized_projection)
    content_values["title"] = "synthetic content must not be stored"
    content_projection = replace(base_projection, sanitized_projection=content_values)
    secret_values = dict(base_projection.sanitized_projection)
    secret_values["payload_reference"] = f"internal://api_key={secret}"
    secret_projection = replace(base_projection, sanitized_projection=secret_values)
    store = InMemoryEvidenceProjectionStore()

    with pytest.raises(ValueError, match="projection_invalid"):
        store.save(content_projection)
    with pytest.raises(ValueError, match="projection_invalid") as caught:
        store.save(secret_projection)

    assert store.get(raw["raw_item_id"]) is None  # type: ignore[arg-type]
    assert secret not in str(caught.value)


def test_trigger_source_has_no_forbidden_runtime_dependencies() -> None:
    root = Path(__file__).parents[1] / "src" / "market_intelligence" / "evidence"
    source = "\n".join(
        (root / name).read_text() for name in ("projection_store.py", "pipeline_trigger.py")
    )
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
    )
    assert all(marker not in source for marker in forbidden)
