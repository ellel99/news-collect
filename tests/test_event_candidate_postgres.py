from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from market_intelligence.db import Base, EventCandidate, EventCandidateEvidence
from market_intelligence.db.models import EventCandidateStatus
from market_intelligence.event_intelligence.service import EventCandidateService

POSTGRES_TEST_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://market_intelligence:local_dev_only@localhost:5432/market_intelligence",
)


@pytest_asyncio.fixture
async def event_engine() -> AsyncIterator[AsyncEngine]:
    schema = f"spec_0039_{uuid.uuid4().hex}"
    engine = create_async_engine(
        POSTGRES_TEST_URL, connect_args={"server_settings": {"search_path": schema}}
    )
    async with engine.connect() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        await connection.commit()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield engine
    finally:
        async with engine.connect() as connection:
            await connection.rollback()
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            await connection.commit()
        await engine.dispose()


@pytest_asyncio.fixture
async def event_session(event_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    factory = async_sessionmaker(event_engine, expire_on_commit=False)
    async with factory() as session:
        yield session


async def create_evidence(
    session: AsyncSession,
    *,
    provider: str,
    provider_item_id: str,
    title: str,
    entity: str = "entity:acme",
    event_time: datetime | None = None,
    official: bool = False,
    canonical_url: str | None = None,
) -> uuid.UUID:
    now = event_time or datetime.now(UTC)
    source_id = (
        await session.execute(
            text(
                """
                INSERT INTO sources (
                  code, name, source_type, access_method, authorization_status, retention_class
                ) VALUES (:code, 'Event Fixture', 'api', 'fake', 'authorized', 'metadata_only')
                RETURNING id
                """
            ),
            {"code": f"event-{uuid.uuid4().hex}"},
        )
    ).scalar_one()
    run_id = (
        await session.execute(
            text(
                "INSERT INTO collection_runs (source_id, started_at, status) "
                "VALUES (:source, :now, 'succeeded') RETURNING id"
            ),
            {"source": source_id, "now": now},
        )
    ).scalar_one()
    raw_id = (
        await session.execute(
            text(
                "INSERT INTO raw_items (source_id, collection_run_id, external_id, fetched_at, "
                "retention_class, parse_status) VALUES "
                "(:source, :run, :external, :now, 'metadata_only', 'parsed') RETURNING id"
            ),
            {"source": source_id, "run": run_id, "external": provider_item_id, "now": now},
        )
    ).scalar_one()
    content_id = (
        await session.execute(
            text(
                """
                INSERT INTO content_items (
                  raw_item_id, source_id, content_kind, external_id, title, canonical_url,
                  body_availability,
                  first_seen_at, deleted_status, metadata
                ) VALUES (
                  :raw, :source, 'article', :external, :title, :canonical_url, 'summary_only', :now,
                  'present', '{}'::jsonb
                ) RETURNING id
                """
            ),
            {
                "raw": raw_id,
                "source": source_id,
                "external": provider_item_id,
                "title": title,
                "canonical_url": canonical_url,
                "now": now,
            },
        )
    ).scalar_one()
    item_type = "sec_filing" if provider == "sec_edgar" else "marketaux_news"
    kind = "disclosure" if provider == "sec_edgar" else "news"
    source_type = "disclosure" if provider == "sec_edgar" else "news"
    evidence_id = (
        await session.execute(
            text(
                """
                INSERT INTO evidence_items (
                  evidence_version, provider, provider_item_type, evidence_kind, source_type,
                  source_id, raw_item_id, content_item_id, provider_item_id, provider_item_hash,
                  event_time, observed_at, access_level, processing_status, official_source_flag,
                  market_data_flag, disclosure_flag, news_signal_flag, content_presence,
                  numeric_presence, entity_refs, asset_refs, topic_refs, errors
                ) VALUES (
                  1, :provider, :item_type, :kind, :source_type, :source, :raw, :content,
                  :external, :hash, :now, :now, 'link_only', 'validated', :official, false,
                  :disclosure, :news, '{}'::jsonb, '{}'::jsonb, CAST(:entities AS jsonb),
                  '[]'::jsonb, '[]'::jsonb, '[]'::jsonb
                ) RETURNING id
                """
            ),
            {
                "provider": provider,
                "item_type": item_type,
                "kind": kind,
                "source_type": source_type,
                "source": source_id,
                "raw": raw_id,
                "content": content_id,
                "external": provider_item_id,
                "hash": uuid.uuid4().hex + uuid.uuid4().hex,
                "now": now,
                "official": official,
                "disclosure": provider == "sec_edgar",
                "news": provider == "marketaux",
                "entities": f'["{entity}"]',
            },
        )
    ).scalar_one()
    await session.commit()
    return evidence_id


@pytest.mark.asyncio
async def test_official_and_coverage_group_with_stable_identity_and_provenance(
    event_session: AsyncSession,
) -> None:
    service = EventCandidateService()
    official_id = await create_evidence(
        event_session,
        provider="sec_edgar",
        provider_item_id="accession-1",
        title="Acme files quarterly report",
        official=True,
    )
    first = await service.process(event_session, official_id)
    await event_session.commit()
    original_key = first.cluster_key
    coverage_id = await create_evidence(
        event_session,
        provider="marketaux",
        provider_item_id="news-1",
        title="Acme files quarterly report",
    )
    second = await service.process(event_session, coverage_id)
    await event_session.commit()
    candidate = await event_session.get(EventCandidate, first.event_candidate_id)
    assert second.event_candidate_id == first.event_candidate_id
    assert second.match_rule.value == "entity_title_time"
    assert candidate is not None
    assert candidate.cluster_key == original_key
    assert candidate.evidence_count == 2
    assert candidate.source_count == 2
    links = (
        await event_session.scalars(
            select(EventCandidateEvidence).where(
                EventCandidateEvidence.event_candidate_id == candidate.id
            )
        )
    ).all()
    assert {link.evidence_item_id for link in links} == {official_id, coverage_id}
    assert all(link.match_rule and link.rule_version == 1 for link in links)


@pytest.mark.asyncio
async def test_repeated_processing_and_association_reactivation_are_idempotent(
    event_session: AsyncSession,
) -> None:
    service = EventCandidateService()
    evidence_id = await create_evidence(
        event_session,
        provider="marketaux",
        provider_item_id="news-repeat",
        title="Acme files report",
    )
    first = await service.process(event_session, evidence_id)
    second = await service.process(event_session, evidence_id)
    assert first.event_candidate_id == second.event_candidate_id
    assert second.status == "existing"
    old_link = await service.deactivate_association(
        event_session, first.event_candidate_id, evidence_id
    )
    await event_session.commit()
    inactive = await event_session.get(EventCandidateEvidence, old_link.id)
    assert inactive is not None and not inactive.active and inactive.removed_at is not None
    old_removed_at = inactive.removed_at
    old_match_rule = inactive.match_rule
    old_rule_version = inactive.rule_version
    orphan = await event_session.get(EventCandidate, first.event_candidate_id)
    assert orphan is not None
    assert orphan.evidence_count == 0
    assert orphan.source_count == 0
    assert orphan.confidence == 0
    assert orphan.importance_score == 0
    assert orphan.status is EventCandidateStatus.REJECTED
    reactivated = await service.process(event_session, evidence_id)
    await event_session.commit()
    links = (
        await event_session.scalars(
            select(EventCandidateEvidence)
            .where(
                EventCandidateEvidence.event_candidate_id == first.event_candidate_id,
                EventCandidateEvidence.evidence_item_id == evidence_id,
            )
            .order_by(EventCandidateEvidence.added_at, EventCandidateEvidence.id)
        )
    ).all()
    assert reactivated.event_candidate_id == first.event_candidate_id
    assert len(links) == 2
    historical = next(link for link in links if link.id == old_link.id)
    current = next(link for link in links if link.id != old_link.id)
    assert not historical.active and historical.removed_at == old_removed_at
    assert historical.match_rule == old_match_rule
    assert historical.rule_version == old_rule_version
    assert current.active and current.removed_at is None
    restored = await event_session.get(EventCandidate, first.event_candidate_id)
    assert restored is not None
    assert restored.evidence_count == 1
    assert restored.source_count == 1
    assert restored.confidence > 0
    assert restored.importance_score > 0
    assert restored.status is EventCandidateStatus.CANDIDATE
    assert await event_session.scalar(select(func.count()).select_from(EventCandidate)) == 1
    assert await event_session.scalar(select(func.count()).select_from(EventCandidateEvidence)) == 2


@pytest.mark.asyncio
async def test_deactivate_refreshes_active_aggregates_and_retains_evidence(
    event_session: AsyncSession,
) -> None:
    service = EventCandidateService()
    first_id = await create_evidence(
        event_session,
        provider="marketaux",
        provider_item_id="aggregate-1",
        title="Acme aggregate event",
    )
    second_id = await create_evidence(
        event_session,
        provider="sec_edgar",
        provider_item_id="aggregate-2",
        title="Acme aggregate event",
        official=True,
    )
    first = await service.process(event_session, first_id)
    second = await service.process(event_session, second_id)
    assert second.event_candidate_id == first.event_candidate_id
    candidate = await event_session.get(EventCandidate, first.event_candidate_id)
    assert candidate is not None
    original_importance = candidate.importance_score
    await service.deactivate_association(event_session, candidate.id, second_id)
    await event_session.flush()
    assert candidate.evidence_count == 1
    assert candidate.source_count == 1
    assert candidate.importance_score < original_importance
    assert await event_session.get(EventCandidate, candidate.id) is not None
    assert (
        await event_session.scalar(
            select(func.count())
            .select_from(EventCandidateEvidence)
            .where(
                EventCandidateEvidence.evidence_item_id == second_id,
                EventCandidateEvidence.active.is_(False),
            )
        )
        == 1
    )
    assert (
        await event_session.scalar(
            text("SELECT count(*) FROM evidence_items WHERE id = :id"), {"id": second_id}
        )
        == 1
    )


@pytest.mark.asyncio
async def test_regroup_preserves_history_and_refreshes_both_candidates(
    event_session: AsyncSession,
) -> None:
    service = EventCandidateService()
    evidence_id = await create_evidence(
        event_session,
        provider="marketaux",
        provider_item_id="regroup-1",
        title="Acme reviewed regroup event",
    )
    original_outcome = await service.process(event_session, evidence_id)
    original = await event_session.get(EventCandidate, original_outcome.event_candidate_id)
    assert original is not None
    target = EventCandidate(
        cluster_key="d" * 64,
        anchor_type="review_target",
        anchor_value_hash="e" * 64,
        strong_identity_hash=None,
        identity_signatures=[],
        title_fingerprints=[],
        event_type="information_event",
        status=EventCandidateStatus.REJECTED,
        canonical_title=None,
        fact_summary=None,
        first_seen_at=original.first_seen_at,
        latest_seen_at=original.latest_seen_at,
        occurred_at=None,
        published_at=None,
        primary_entity=None,
        entities=[],
        companies=[],
        assets=[],
        sectors=[],
        topics=[],
        evidence_count=0,
        source_count=0,
        confidence=0,
        importance_score=0,
        importance_reasons=["no_active_evidence:0"],
    )
    event_session.add(target)
    await event_session.flush()
    result = await service.regroup_association(event_session, evidence_id, original.id, target.id)
    await event_session.commit()
    assert result.status == "regrouped"
    assert result.event_candidate_id == target.id
    assert original.evidence_count == 0 and original.source_count == 0
    assert original.status is EventCandidateStatus.REJECTED
    assert target.evidence_count == 1 and target.source_count == 1
    assert target.status is EventCandidateStatus.CANDIDATE
    links = (
        await event_session.scalars(
            select(EventCandidateEvidence)
            .where(EventCandidateEvidence.evidence_item_id == evidence_id)
            .order_by(EventCandidateEvidence.added_at, EventCandidateEvidence.id)
        )
    ).all()
    assert len(links) == 2
    historical = next(link for link in links if link.event_candidate_id == original.id)
    current = next(link for link in links if link.event_candidate_id == target.id)
    assert not historical.active and historical.removed_at is not None
    assert historical.match_rule == original_outcome.match_rule.value
    assert current.active and current.removed_at is None
    assert current.match_rule == "reviewed_regroup"
    assert (
        await event_session.scalar(
            text("SELECT count(*) FROM evidence_items WHERE id = :id"), {"id": evidence_id}
        )
        == 1
    )


@pytest.mark.asyncio
async def test_stronger_later_identity_enriches_without_rewriting_cluster_key(
    event_session: AsyncSession,
) -> None:
    service = EventCandidateService()
    news_id = await create_evidence(
        event_session,
        provider="marketaux",
        provider_item_id="news-before-official",
        title="Acme files annual report",
    )
    first = await service.process(event_session, news_id)
    official_id = await create_evidence(
        event_session,
        provider="sec_edgar",
        provider_item_id="accession-strong",
        title="Acme files annual report",
        official=True,
    )
    second = await service.process(event_session, official_id)
    await event_session.commit()
    candidate = await event_session.get(EventCandidate, first.event_candidate_id)
    assert second.event_candidate_id == first.event_candidate_id
    assert candidate is not None
    assert candidate.cluster_key == first.cluster_key
    assert candidate.strong_identity_hash is not None


@pytest.mark.asyncio
async def test_same_company_different_fact_and_expired_window_remain_separate(
    event_session: AsyncSession,
) -> None:
    service = EventCandidateService()
    first_id = await create_evidence(
        event_session,
        provider="marketaux",
        provider_item_id="fact-1",
        title="Acme files report",
    )
    different_id = await create_evidence(
        event_session,
        provider="marketaux",
        provider_item_id="fact-2",
        title="Acme opens factory",
    )
    expired_id = await create_evidence(
        event_session,
        provider="sec_edgar",
        provider_item_id="fact-3",
        title="Acme files report",
        event_time=datetime.now(UTC) + timedelta(days=3),
        official=True,
    )
    outcomes = [
        await service.process(event_session, value)
        for value in (first_id, different_id, expired_id)
    ]
    await event_session.commit()
    assert len({item.event_candidate_id for item in outcomes}) == 3


@pytest.mark.asyncio
async def test_pair_uniqueness_does_not_impose_global_evidence_ownership(
    event_session: AsyncSession,
) -> None:
    service = EventCandidateService()
    evidence_id = await create_evidence(
        event_session,
        provider="marketaux",
        provider_item_id="multi-link",
        title="Acme event",
    )
    outcome = await service.process(event_session, evidence_id)
    original = await event_session.get(EventCandidate, outcome.event_candidate_id)
    assert original is not None
    other = EventCandidate(
        cluster_key="f" * 64,
        anchor_type="test",
        anchor_value_hash="e" * 64,
        strong_identity_hash=None,
        identity_signatures=[],
        title_fingerprints=[],
        event_type="information_event",
        status=EventCandidateStatus.CANDIDATE,
        canonical_title=None,
        fact_summary=None,
        first_seen_at=original.first_seen_at,
        latest_seen_at=original.latest_seen_at,
        occurred_at=None,
        published_at=None,
        primary_entity=None,
        entities=[],
        companies=[],
        assets=[],
        sectors=[],
        topics=[],
        evidence_count=1,
        source_count=1,
        confidence=0.1,
        importance_score=10,
        importance_reasons=["test"],
    )
    event_session.add(other)
    await event_session.flush()
    event_session.add(
        EventCandidateEvidence(
            event_candidate_id=other.id,
            evidence_item_id=evidence_id,
            match_rule="reviewed_regroup",
            rule_version=1,
            official_source=False,
            active=False,
            removed_at=datetime.now(UTC),
        )
    )
    await event_session.commit()
    assert (
        await event_session.scalar(
            select(func.count())
            .select_from(EventCandidateEvidence)
            .where(EventCandidateEvidence.evidence_item_id == evidence_id)
        )
        == 2
    )
    event_session.add(
        EventCandidateEvidence(
            event_candidate_id=other.id,
            evidence_item_id=evidence_id,
            match_rule="duplicate",
            rule_version=1,
            official_source=False,
            active=False,
            removed_at=datetime.now(UTC),
        )
    )
    await event_session.commit()
    assert (
        await event_session.scalar(
            select(func.count())
            .select_from(EventCandidateEvidence)
            .where(
                EventCandidateEvidence.event_candidate_id == other.id,
                EventCandidateEvidence.evidence_item_id == evidence_id,
                EventCandidateEvidence.active.is_(False),
            )
        )
        == 2
    )
    event_session.add(
        EventCandidateEvidence(
            event_candidate_id=other.id,
            evidence_item_id=evidence_id,
            match_rule="active_regroup",
            rule_version=2,
            official_source=False,
            active=True,
            removed_at=None,
        )
    )
    await event_session.commit()
    with pytest.raises(ValueError, match="event_candidate_association_ambiguous"):
        await service.process(event_session, evidence_id)
    assert await event_session.scalar(select(func.count()).select_from(EventCandidate)) == 2
    assert (
        await event_session.scalar(
            select(func.count())
            .select_from(EventCandidateEvidence)
            .where(
                EventCandidateEvidence.evidence_item_id == evidence_id,
                EventCandidateEvidence.active.is_(True),
            )
        )
        == 2
    )
    event_session.add(
        EventCandidateEvidence(
            event_candidate_id=other.id,
            evidence_item_id=evidence_id,
            match_rule="duplicate_active",
            rule_version=2,
            official_source=False,
            active=True,
            removed_at=None,
        )
    )
    with pytest.raises(IntegrityError):
        await event_session.commit()


@pytest.mark.asyncio
async def test_concurrent_same_evidence_creation_converges(event_engine: AsyncEngine) -> None:
    factory = async_sessionmaker(event_engine, expire_on_commit=False)
    async with factory() as setup:
        evidence_id = await create_evidence(
            setup,
            provider="marketaux",
            provider_item_id="concurrent",
            title="Acme concurrency event",
        )

    async def run_once() -> Any:
        async with factory() as session:
            value = await EventCandidateService().process(session, evidence_id)
            await session.commit()
            return value

    results = await asyncio.gather(run_once(), run_once())
    async with factory() as check:
        assert results[0].event_candidate_id == results[1].event_candidate_id
        assert await check.scalar(select(func.count()).select_from(EventCandidate)) == 1
        assert await check.scalar(select(func.count()).select_from(EventCandidateEvidence)) == 1


@pytest.mark.asyncio
async def test_0005_migration_round_trip_and_identity_trigger() -> None:
    schema = f"spec_0039_migration_{uuid.uuid4().hex}"
    engine = create_async_engine(
        POSTGRES_TEST_URL, connect_args={"server_settings": {"search_path": schema}}
    )
    async with engine.connect() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        await connection.commit()

    def round_trip(sync_connection: Any) -> None:
        existing = [
            table
            for table in Base.metadata.sorted_tables
            if table.name not in {"event_candidates", "event_candidate_evidence", "impact_analyses"}
        ]
        Base.metadata.create_all(sync_connection, tables=existing)
        revision = ScriptDirectory.from_config(Config("alembic.ini")).get_revision("0005")
        assert revision is not None and revision.down_revision == "0004"
        with Operations.context(MigrationContext.configure(sync_connection)):
            revision.module.upgrade()
            assert inspect(sync_connection).has_table("event_candidates")
            candidate_id = sync_connection.execute(
                text(
                    """
                    INSERT INTO event_candidates (
                      cluster_key, anchor_type, anchor_value_hash, event_type, status,
                      first_seen_at, latest_seen_at, evidence_count, source_count, confidence,
                      importance_score
                    ) VALUES (
                      :key, 'test', :anchor, 'information_event', 'candidate', now(), now(),
                      1, 1, 0.5, 10
                    ) RETURNING id
                    """
                ),
                {"key": "a" * 64, "anchor": "b" * 64},
            ).scalar_one()
            nested = sync_connection.begin_nested()
            try:
                sync_connection.execute(
                    text("UPDATE event_candidates SET cluster_key = :key WHERE id = :id"),
                    {"key": "c" * 64, "id": candidate_id},
                )
            except DBAPIError:
                nested.rollback()
            else:
                nested.commit()
                pytest.fail("event_candidate_identity_update_was_not_rejected")
            revision.module.downgrade()
            assert not inspect(sync_connection).has_table("event_candidates")
            revision.module.upgrade()
            assert inspect(sync_connection).has_table("event_candidate_evidence")

    try:
        async with engine.begin() as connection:
            await connection.run_sync(round_trip)
    finally:
        async with engine.connect() as connection:
            await connection.rollback()
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            await connection.commit()
        await engine.dispose()
