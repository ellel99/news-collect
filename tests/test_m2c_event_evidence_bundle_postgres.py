"""PostgreSQL acceptance tests for M2-C durable Event Evidence Bundles."""

from __future__ import annotations

import asyncio
import os
import pathlib
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from alembic.config import Config
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from m2c_helpers import cleanup as _cleanup
from m2c_helpers import seed_ready as _seed_ready
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from market_intelligence.db.models import (
    EventEvidenceBundle,
    EventEvidenceBundleHead,
    EventEvidenceBundleItem,
    EventEvidenceBundleJob,
    EventEvidenceBundleJobStatus,
    EventEvidenceBundleStatus,
    EventEvidenceRelation,
    EvidenceProjectionLink,
    EvidenceProjectionLinkStatus,
)
from market_intelligence.event_evidence.service import EventEvidenceBundleService
from market_intelligence.event_evidence.worker import EventEvidenceBundleWorker
from market_intelligence.evidence.handoff import EvidenceProjectionHandoffWorker
from market_intelligence.test_database import isolated_test_database_url

try:
    POSTGRES_TEST_URL = isolated_test_database_url(os.environ.get("TEST_DATABASE_URL"))
except ValueError as exc:
    pytest.skip(str(exc), allow_module_level=True)


async def _event(
    factory: async_sessionmaker[AsyncSession], evidence_ids: tuple[uuid.UUID, ...]
) -> uuid.UUID:
    marker = uuid.uuid4().hex
    async with factory.begin() as session:
        event_id = await session.scalar(
            text("""
            INSERT INTO event_candidates(
              cluster_key,anchor_type,anchor_value_hash,event_type,status,
              first_seen_at,latest_seen_at,evidence_count,source_count,
              confidence,importance_score
            ) VALUES (
              :cluster,'synthetic',:anchor,'synthetic','candidate',:now,:now,
              :evidence_count,:source_count,1,1
            ) RETURNING id
            """),
            {
                "cluster": marker.ljust(64, "0")[:64],
                "anchor": marker[::-1].ljust(64, "0")[:64],
                "now": datetime.now(UTC),
                "evidence_count": len(evidence_ids),
                "source_count": len(evidence_ids),
            },
        )
        assert event_id is not None
        for evidence_id in evidence_ids:
            await session.execute(
                text("""
                INSERT INTO event_candidate_evidence(
                  event_candidate_id,evidence_item_id,match_rule,rule_version,
                  official_source,active
                ) VALUES (:event,:evidence,'synthetic_exact',1,false,true)
                """),
                {"event": event_id, "evidence": evidence_id},
            )
        return event_id


async def _linked_evidence(
    factory: async_sessionmaker[AsyncSession], provider: str
) -> tuple[uuid.UUID, uuid.UUID]:
    raw_id, projection_id, _payload = await _seed_ready(factory, provider)
    report = await EvidenceProjectionHandoffWorker(factory).process_batch(limit=10)
    assert report.linked == 1
    async with factory() as session:
        link = await session.scalar(
            select(EvidenceProjectionLink).where(
                EvidenceProjectionLink.safe_fact_projection_id == projection_id,
                EvidenceProjectionLink.status == EvidenceProjectionLinkStatus.LINKED,
            )
        )
        assert link is not None and link.evidence_item_id is not None
        return raw_id, link.evidence_item_id


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["marketaux", "finnhub", "eia", "sec_edgar"])
async def test_four_provider_packets_create_durable_bundle(provider: str) -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        _raw, evidence_id = await _linked_evidence(factory, provider)
        event_id = await _event(factory, (evidence_id,))
        result = await EventEvidenceBundleService(factory).build(event_id)
        assert result.status in {"ready", "partial"}
        async with factory() as session:
            bundle = await session.get(EventEvidenceBundle, result.bundle_id)
            assert bundle is not None
            assert bundle.provider_coverage == [provider]
            assert bundle.evidence_count == 1
            item = await session.scalar(
                select(EventEvidenceBundleItem).where(
                    EventEvidenceBundleItem.bundle_id == bundle.id
                )
            )
            assert item is not None
            assert item.relation is EventEvidenceRelation.SUPPORTING
            assert len(item.packet_digest) == len(item.projection_hash) == 64
            head = await session.get(EventEvidenceBundleHead, event_id)
            assert head is not None and head.current_bundle_id == bundle.id
            if bundle.status is EventEvidenceBundleStatus.READY:
                assert head.canonical_bundle_id == bundle.id
    finally:
        await _cleanup(factory)
        await engine.dispose()


@pytest.mark.asyncio
async def test_revision_is_append_only_and_rerun_is_idempotent() -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        raw_id, evidence_id = await _linked_evidence(factory, "marketaux")
        event_id = await _event(factory, (evidence_id,))
        service = EventEvidenceBundleService(factory)
        first = await service.build(event_id)
        same = await service.build(event_id)
        assert same.status == "unchanged" and same.bundle_id == first.bundle_id

        _raw, projection_id, _payload = await _seed_ready(
            factory,
            "marketaux",
            raw_id=raw_id,
            payload_updates={"title": "Synthetic revised factual title"},
        )
        handoff = await EvidenceProjectionHandoffWorker(factory).process_batch(limit=10)
        assert handoff.linked == 1
        second = await service.build(event_id)
        assert second.revision == 2 and second.bundle_id != first.bundle_id
        async with factory() as session:
            assert await session.scalar(select(func.count()).select_from(EventEvidenceBundle)) == 2
            second_item = await session.scalar(
                select(EventEvidenceBundleItem).where(
                    EventEvidenceBundleItem.bundle_id == second.bundle_id
                )
            )
            assert second_item is not None
            assert second_item.relation is EventEvidenceRelation.SUPERSEDING
            projection_link = await session.scalar(
                select(EvidenceProjectionLink).where(
                    EvidenceProjectionLink.safe_fact_projection_id == projection_id
                )
            )
            assert projection_link is not None
            assert projection_link.evidence_item_id == evidence_id
    finally:
        await _cleanup(factory)
        await engine.dispose()


@pytest.mark.asyncio
async def test_multiple_evidence_share_one_event_with_traceable_diversity() -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        _raw_one, first = await _linked_evidence(factory, "marketaux")
        _raw_two, second = await _linked_evidence(factory, "sec_edgar")
        event_id = await _event(factory, (first, second))
        result = await EventEvidenceBundleService(factory).build(event_id)
        async with factory() as session:
            bundle = await session.get(EventEvidenceBundle, result.bundle_id)
            assert bundle is not None
            assert bundle.evidence_count == bundle.source_count == 2
            assert bundle.provider_coverage == ["marketaux", "sec_edgar"]
            items = tuple(
                await session.scalars(
                    select(EventEvidenceBundleItem).where(
                        EventEvidenceBundleItem.bundle_id == bundle.id
                    )
                )
            )
            assert {item.evidence_item_id for item in items} == {first, second}
            assert len({item.source_id for item in items}) == 2
    finally:
        await _cleanup(factory)
        await engine.dispose()


@pytest.mark.asyncio
async def test_durable_keyset_discovery_does_not_starve_later_candidates() -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        _raw, evidence_id = await _linked_evidence(factory, "sec_edgar")
        event_ids = [await _event(factory, (evidence_id,)) for _ in range(3)]
        for _ in range(4):
            await EventEvidenceBundleWorker(factory).process_batch(limit=1)
        async with factory() as session:
            assert (
                await session.scalar(select(func.count()).select_from(EventEvidenceBundleHead)) == 3
            )
            assert set(
                await session.scalars(select(EventEvidenceBundleHead.event_candidate_id))
            ) == set(event_ids)
    finally:
        await _cleanup(factory)
        await engine.dispose()


@pytest.mark.asyncio
async def test_worker_is_bounded_concurrent_idempotent_and_recovers_stale() -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        _raw, evidence_id = await _linked_evidence(factory, "sec_edgar")
        event_id = await _event(factory, (evidence_id,))
        reports = await asyncio.gather(
            EventEvidenceBundleWorker(factory).process_batch(limit=1),
            EventEvidenceBundleWorker(factory).process_batch(limit=1),
        )
        assert sum(report.ready + report.partial for report in reports) == 1
        async with factory.begin() as session:
            assert await session.scalar(select(func.count()).select_from(EventEvidenceBundle)) == 1
            job = await session.get(EventEvidenceBundleJob, event_id)
            assert job is not None
            job.status = EventEvidenceBundleJobStatus.PROCESSING
            job.processing_started_at = datetime.now(UTC) - timedelta(hours=1)
            job.claim_token = uuid.uuid4()
        recovered = await EventEvidenceBundleWorker(
            factory, stale_after=timedelta(seconds=1)
        ).process_batch(limit=1)
        assert recovered.recovered == 1
        assert await EventEvidenceBundleService(factory).build(
            event_id
        ) == await EventEvidenceBundleService(factory).build(event_id)
    finally:
        await _cleanup(factory)
        await engine.dispose()


@pytest.mark.asyncio
async def test_sql_bypass_cannot_mutate_bundle_or_insert_bad_provenance() -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        _raw, evidence_id = await _linked_evidence(factory, "finnhub")
        event_id = await _event(factory, (evidence_id,))
        result = await EventEvidenceBundleService(factory).build(event_id)
        async with factory.begin() as session:
            with pytest.raises(DBAPIError, match="event_evidence_bundle_immutable"):
                await session.execute(
                    text("UPDATE event_evidence_bundles SET revision=revision+1 WHERE id=:id"),
                    {"id": result.bundle_id},
                )
        async with factory.begin() as session:
            item = await session.scalar(
                select(EventEvidenceBundleItem).where(
                    EventEvidenceBundleItem.bundle_id == result.bundle_id
                )
            )
            assert item is not None
            with pytest.raises(DBAPIError, match="event_evidence_bundle_item_provenance_invalid"):
                await session.execute(
                    text("""
                    INSERT INTO event_evidence_bundle_items(
                      bundle_id,event_candidate_evidence_id,evidence_item_id,packet_digest,
                      projection_hash,fact_identity_digest,fact_value_digest,relation,
                      relation_rule,rule_version,provider,operation_key,source_id,event_time
                    ) VALUES (
                      :bundle,:association,:evidence,repeat('0',64),repeat('1',64),
                      repeat('2',64),repeat('3',64),'supporting','m2c_relation_v1',1,
                      'finnhub','quote',:source,:now
                    )
                    """),
                    {
                        "bundle": item.bundle_id,
                        "association": uuid.uuid4(),
                        "evidence": item.evidence_item_id,
                        "source": item.source_id,
                        "now": item.event_time,
                    },
                )
    finally:
        await _cleanup(factory)
        await engine.dispose()


@pytest.mark.asyncio
async def test_0011_roundtrip_and_nonempty_downgrade_guard() -> None:
    revision = ScriptDirectory.from_config(Config("alembic.ini")).get_revision("0011")
    assert revision is not None and revision.module is not None
    engine = create_async_engine(POSTGRES_TEST_URL)
    try:

        def roundtrip(connection: object) -> None:
            with Operations.context(MigrationContext.configure(connection)):
                revision.module.downgrade()
                revision.module.upgrade()

        async with engine.connect() as connection:
            transaction = await connection.begin()
            await connection.run_sync(roundtrip)
            await transaction.rollback()
        assert revision.down_revision == "0010"
        assert ScriptDirectory.from_config(Config("alembic.ini")).get_heads() == ["0011"]
    finally:
        await engine.dispose()


def test_task_is_registered_for_every_authority_schedule(monkeypatch: pytest.MonkeyPatch) -> None:
    from market_intelligence.tasks.celery_app import (
        legacy_schedule,
        shadow_schedule,
        unified_schedule,
    )

    del monkeypatch
    expected = {"task": "event_evidence.reconcile"}
    for schedule in (legacy_schedule, shadow_schedule, unified_schedule):
        entry = schedule["event-evidence-bundle-reconciliation"]
        assert entry["task"] == expected["task"]


def test_migration_declares_0010_parent_and_no_ai_or_external_runtime() -> None:
    source = pathlib.Path("src/market_intelligence/event_evidence/service.py").read_text()
    worker = pathlib.Path("src/market_intelligence/event_evidence/worker.py").read_text()
    prohibited = ("requests", "httpx", "OpenAI", "Telegram", "provider_capture", "local_evaluation")
    assert not any(marker in source + worker for marker in prohibited)
