from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from market_intelligence.db import Base, ImpactAnalysisRecord
from market_intelligence.event_intelligence.analysis import (
    DeterministicMockImpactAnalyzer,
    ImpactAnalysis,
    ImpactDirection,
    ImpactHorizon,
    ImpactRequest,
)
from market_intelligence.event_intelligence.fact_layer import FactLayerBuilder, FactSnapshot
from market_intelligence.event_intelligence.persistence import AnalyzerIdentity, ImpactAnalysisStore
from market_intelligence.event_intelligence.runtime import (
    EventProcessingRuntime,
    EventRuntimeStatus,
)
from market_intelligence.event_intelligence.service import EventCandidateService

POSTGRES_TEST_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://market_intelligence:local_dev_only@localhost:5432/market_intelligence",
)


@pytest_asyncio.fixture
async def runtime_session() -> AsyncIterator[AsyncSession]:
    schema = f"spec_0040_{uuid.uuid4().hex}"
    engine = create_async_engine(
        POSTGRES_TEST_URL, connect_args={"server_settings": {"search_path": schema}}
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


async def _evidence(
    session: AsyncSession,
    *,
    provider: str = "marketaux",
    external_id: str = "news-1",
    title: str = "Acme announces audited infrastructure expansion",
    official: bool = False,
) -> uuid.UUID:
    now = datetime.now(UTC)
    source_id = (
        await session.execute(
            text(
                "INSERT INTO sources (code,name,source_type,access_method,authorization_status,"
                "retention_class) VALUES "
                "(:code,'Fixture','api','fake','authorized','metadata_only') "
                "RETURNING id"
            ),
            {"code": uuid.uuid4().hex},
        )
    ).scalar_one()
    run_id = (
        await session.execute(
            text(
                "INSERT INTO collection_runs (source_id,started_at,status) "
                "VALUES (:source,:now,'succeeded') RETURNING id"
            ),
            {"source": source_id, "now": now},
        )
    ).scalar_one()
    raw_id = (
        await session.execute(
            text(
                "INSERT INTO raw_items (source_id,collection_run_id,external_id,fetched_at,"
                "retention_class,parse_status) VALUES (:source,:run,:external,:now,"
                "'metadata_only','parsed') RETURNING id"
            ),
            {"source": source_id, "run": run_id, "external": external_id, "now": now},
        )
    ).scalar_one()
    content_id = (
        await session.execute(
            text(
                "INSERT INTO content_items (raw_item_id,source_id,content_kind,external_id,title,"
                "body_availability,first_seen_at,deleted_status,metadata) VALUES "
                "(:raw,:source,'article',:external,:title,'summary_only',:now,'present','{}') "
                "RETURNING id"
            ),
            {
                "raw": raw_id,
                "source": source_id,
                "external": external_id,
                "title": title,
                "now": now,
            },
        )
    ).scalar_one()
    evidence_id = (
        await session.execute(
            text(
                "INSERT INTO evidence_items (evidence_version,provider,provider_item_type,"
                "evidence_kind,source_type,source_id,raw_item_id,content_item_id,provider_item_id,"
                "provider_item_hash,event_time,observed_at,access_level,processing_status,"
                "official_source_flag,market_data_flag,disclosure_flag,news_signal_flag,"
                "content_presence,numeric_presence,entity_refs,asset_refs,topic_refs,errors) "
                "VALUES (1,:provider,:provider_item_type,'news','news',:source,:raw,:content,"
                ":external,:hash,"
                ":now,:now,'link_only','validated',:official,false,false,:news_signal,'{}','{}',"
                "'[\"entity:acme\"]','[\"asset:acme\"]','[\"topic:infrastructure\"]','[]') "
                "RETURNING id"
            ),
            {
                "provider": provider,
                "provider_item_type": ("eia_energy_timeseries" if official else "marketaux_news"),
                "source": source_id,
                "raw": raw_id,
                "content": content_id,
                "external": external_id,
                "hash": uuid.uuid4().hex + uuid.uuid4().hex,
                "now": now,
                "official": official,
                "news_signal": not official,
            },
        )
    ).scalar_one()
    await session.commit()
    return evidence_id


@pytest.mark.asyncio
async def test_runtime_is_idempotent_and_persists_mock_analysis(
    runtime_session: AsyncSession,
) -> None:
    evidence_id = await _evidence(runtime_session)
    runtime = EventProcessingRuntime()
    identity = AnalyzerIdentity("deterministic_mock", "mock-v1")
    first = await runtime.process_evidence(
        runtime_session,
        evidence_id,
        analyzer=DeterministicMockImpactAnalyzer(),
        analyzer_identity=identity,
    )
    second = await runtime.process_evidence(
        runtime_session,
        evidence_id,
        analyzer=DeterministicMockImpactAnalyzer(),
        analyzer_identity=identity,
    )
    await runtime_session.commit()
    assert first.status is EventRuntimeStatus.PASS
    assert second.status is EventRuntimeStatus.NO_CHANGE
    assert first.analysis_id == second.analysis_id
    assert await runtime_session.scalar(select(func.count()).select_from(ImpactAnalysisRecord)) == 1


@pytest.mark.asyncio
async def test_fact_layer_aggregates_official_and_news_with_provenance_and_uncertainty(
    runtime_session: AsyncSession,
) -> None:
    news = await _evidence(runtime_session, external_id="coverage")
    official = await _evidence(
        runtime_session,
        provider="sec_edgar",
        external_id="official",
        title="Acme announces audited infrastructure expansion - official filing",
        official=True,
    )
    service = EventCandidateService()
    first = await service.process(runtime_session, news)
    second = await service.process(runtime_session, official)
    assert second.event_candidate_id != first.event_candidate_id
    await service.regroup_association(
        runtime_session, official, second.event_candidate_id, first.event_candidate_id
    )
    fact = await FactLayerBuilder().build(runtime_session, first.event_candidate_id)
    assert fact.evidence_count == 2
    assert fact.source_count == 2
    assert fact.official_evidence_present
    assert len(fact.evidence_refs) == 2
    assert "cross_source" in fact.corroboration
    assert "multiple_content_safe_titles" in fact.contradictions
    assert all("raw" not in value for value in fact.provenance_summary)


@pytest.mark.asyncio
async def test_changed_fact_creates_new_version_and_preserves_previous(
    runtime_session: AsyncSession,
) -> None:
    first_id = await _evidence(runtime_session, external_id="version-1")
    candidate = await EventCandidateService().process(runtime_session, first_id)
    builder = FactLayerBuilder()
    store = ImpactAnalysisStore()
    identity = AnalyzerIdentity("deterministic_mock", "mock-v1")
    fact_one = await builder.build(runtime_session, candidate.event_candidate_id)
    analysis = await DeterministicMockImpactAnalyzer().analyze(_request(fact_one))
    one = await store.persist_valid(runtime_session, fact_one, identity, analysis)
    second_id = await _evidence(
        runtime_session, provider="sec_edgar", external_id="version-2", official=True
    )
    await EventCandidateService().process(runtime_session, second_id)
    fact_two = await builder.build(runtime_session, candidate.event_candidate_id)
    two = await store.persist_valid(runtime_session, fact_two, identity, analysis)
    await runtime_session.commit()
    assert fact_one.snapshot_hash != fact_two.snapshot_hash
    assert (one.analysis_version, two.analysis_version) == (1, 2)
    rows = (
        await runtime_session.scalars(
            select(ImpactAnalysisRecord).order_by(ImpactAnalysisRecord.analysis_version)
        )
    ).all()
    assert len(rows) == 2
    assert rows[1].supersedes_analysis_id == rows[0].id


@pytest.mark.asyncio
async def test_invalid_analysis_is_rejected_without_valid_persistence(
    runtime_session: AsyncSession,
) -> None:
    evidence_id = await _evidence(runtime_session, external_id="invalid")
    candidate = await EventCandidateService().process(runtime_session, evidence_id)
    fact = await FactLayerBuilder().build(runtime_session, candidate.event_candidate_id)
    invalid = ImpactAnalysis(
        (), (), (), ImpactDirection.POSITIVE, ImpactHorizon.IMMEDIATE, (), 2.0, "BUY", (), False, 1
    )
    result = await ImpactAnalysisStore().persist_valid(
        runtime_session, fact, AnalyzerIdentity("mock", "invalid"), invalid
    )
    assert result.status == "invalid"
    assert await runtime_session.scalar(select(func.count()).select_from(ImpactAnalysisRecord)) == 0


def _request(fact: FactSnapshot) -> ImpactRequest:
    return ImpactRequest(
        fact.event_candidate_id,
        fact.what_happened,
        fact.evidence_count,
        fact.source_count,
        fact.official_evidence_present,
        fact.primary_entities,
        fact.assets,
        fact.sectors,
        fact.uncertainty,
    )
