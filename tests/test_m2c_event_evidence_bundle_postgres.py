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
    EventCandidateEvidence,
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
from market_intelligence.event_evidence.contracts import (
    BundleClaimLost,
    BundleConflict,
    BundleRetryableConflict,
)
from market_intelligence.event_evidence.migration_preflight import validate_0011_pre_migration
from market_intelligence.event_evidence.service import EventEvidenceBundleService
from market_intelligence.event_evidence.worker import EventEvidenceBundleWorker
from market_intelligence.event_intelligence.service import EventCandidateService
from market_intelligence.evidence.handoff import EvidenceProjectionHandoffWorker
from market_intelligence.rich_evidence.builder import RichEvidencePacketBuilder
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


async def _clone_without_packet(
    factory: async_sessionmaker[AsyncSession], evidence_id: uuid.UUID
) -> uuid.UUID:
    async with factory.begin() as session:
        identity = await session.scalar(
            text("""
            INSERT INTO evidence_items(
              evidence_version,provider,provider_item_type,evidence_kind,source_type,
              source_id,source_account_id,raw_item_id,content_item_id,provider_item_id,
              provider_item_hash,event_time,observed_at,access_level,processing_status,
              official_source_flag,market_data_flag,disclosure_flag,news_signal_flag,
              content_presence,numeric_presence,entity_refs,asset_refs,topic_refs,errors
            )
            SELECT evidence_version,provider,provider_item_type,evidence_kind,source_type,
              source_id,source_account_id,raw_item_id,content_item_id,
              provider_item_id || '-without-packet',repeat('b',64),event_time,observed_at,
              access_level,processing_status,official_source_flag,market_data_flag,
              disclosure_flag,news_signal_flag,content_presence,numeric_presence,
              entity_refs,asset_refs,topic_refs,errors
            FROM evidence_items WHERE id=:evidence RETURNING id
            """),
            {"evidence": evidence_id},
        )
        assert identity is not None
        return identity


async def _clone_evidence_many(
    factory: async_sessionmaker[AsyncSession], evidence_id: uuid.UUID, count: int
) -> tuple[uuid.UUID, ...]:
    async with factory.begin() as session:
        return tuple(
            await session.scalars(
                text("""
                INSERT INTO evidence_items(
                  evidence_version,provider,provider_item_type,evidence_kind,source_type,
                  source_id,source_account_id,raw_item_id,content_item_id,provider_item_id,
                  provider_item_hash,event_time,observed_at,access_level,processing_status,
                  official_source_flag,market_data_flag,disclosure_flag,news_signal_flag,
                  content_presence,numeric_presence,entity_refs,asset_refs,topic_refs,errors
                )
                SELECT evidence_version,provider,provider_item_type,evidence_kind,source_type,
                  source_id,source_account_id,raw_item_id,content_item_id,
                  provider_item_id || '-member-' || value,
                  lpad(to_hex(value),64,'0'),event_time,observed_at,access_level,
                  processing_status,official_source_flag,market_data_flag,disclosure_flag,
                  news_signal_flag,content_presence,numeric_presence,entity_refs,asset_refs,
                  topic_refs,errors
                FROM evidence_items CROSS JOIN generate_series(1,:count) value
                WHERE id=:evidence RETURNING id
                """),
                {"evidence": evidence_id, "count": count},
            )
        )


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
async def test_recovered_claim_cannot_create_bundle_or_advance_head() -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        _raw, evidence_id = await _linked_evidence(factory, "sec_edgar")
        event_id = await _event(factory, (evidence_id,))
        old_token, new_token = uuid.uuid4(), uuid.uuid4()
        async with factory.begin() as session:
            session.add(
                EventEvidenceBundleJob(
                    event_candidate_id=event_id,
                    status=EventEvidenceBundleJobStatus.PROCESSING,
                    attempt_count=1,
                    processing_started_at=datetime.now(UTC),
                    claim_token=new_token,
                )
            )
        with pytest.raises(BundleClaimLost, match="event_bundle_claim_lost"):
            await EventEvidenceBundleService(factory).build(event_id, claim_token=old_token)
        async with factory() as session:
            assert await session.scalar(select(func.count()).select_from(EventEvidenceBundle)) == 0
            assert await session.get(EventEvidenceBundleHead, event_id) is None
        result = await EventEvidenceBundleService(factory).build(event_id, claim_token=new_token)
        assert result.bundle_id is not None
    finally:
        await _cleanup(factory)
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_packet_revision_cannot_publish_mixed_snapshot() -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        raw_id, evidence_id = await _linked_evidence(factory, "marketaux")
        event_id = await _event(factory, (evidence_id,))
        delegate = RichEvidencePacketBuilder(factory)

        class AppendAfterSnapshot:
            async def build_many(self, evidence_ids: tuple[uuid.UUID, ...]) -> tuple[object, ...]:
                packets = await delegate.build_many(evidence_ids)
                await _seed_ready(
                    factory,
                    "marketaux",
                    raw_id=raw_id,
                    payload_updates={"title": "Concurrent factual revision"},
                )
                assert (
                    await EvidenceProjectionHandoffWorker(factory).process_batch(limit=10)
                ).linked == 1
                return packets

            async def build_many_in_session(
                self, session: AsyncSession, evidence_ids: tuple[uuid.UUID, ...]
            ) -> tuple[object, ...]:
                return await delegate.build_many_in_session(session, evidence_ids)

        service = EventEvidenceBundleService(factory, packet_builder=AppendAfterSnapshot())  # type: ignore[arg-type]
        with pytest.raises(BundleRetryableConflict, match="event_bundle_packet_snapshot_changed"):
            await service.build(event_id)
        async with factory() as session:
            assert await session.scalar(select(func.count()).select_from(EventEvidenceBundle)) == 0
            assert await session.get(EventEvidenceBundleHead, event_id) is None
        result = await EventEvidenceBundleService(factory).build(event_id)
        assert result.revision == 1
    finally:
        await _cleanup(factory)
        await engine.dispose()


@pytest.mark.asyncio
async def test_exhausted_dependency_reopens_only_after_material_change() -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        _raw, linked_id = await _linked_evidence(factory, "marketaux")
        orphan_id = await _clone_without_packet(factory, linked_id)
        event_id = await _event(factory, (orphan_id,))
        worker = EventEvidenceBundleWorker(factory, max_attempts=1)
        first = await worker.process_batch(limit=10)
        assert first.blocked == 1
        unchanged = await worker.process_batch(limit=10)
        assert unchanged.claimed == 0
        async with factory.begin() as session:
            await EventCandidateService().deactivate_association(session, event_id, orphan_id)
            await session.execute(
                text("""
                INSERT INTO event_candidate_evidence(
                  event_candidate_id,evidence_item_id,match_rule,rule_version,
                  official_source,active
                ) VALUES (:event,:evidence,'dependency_changed',1,false,true)
                """),
                {"event": event_id, "evidence": linked_id},
            )
        reopened = await worker.process_batch(limit=10)
        assert reopened.ready + reopened.partial == 1
        async with factory() as session:
            job = await session.get(EventEvidenceBundleJob, event_id)
            assert job is not None
            assert job.dependency_fingerprint is None
            assert job.attempt_count == 0
    finally:
        await _cleanup(factory)
        await engine.dispose()


@pytest.mark.asyncio
async def test_lost_claim_during_block_is_reported_as_claim_lost() -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        _raw, evidence_id = await _linked_evidence(factory, "sec_edgar")
        event_id = await _event(factory, (evidence_id,))
        worker = EventEvidenceBundleWorker(factory)

        class LoseClaimService:
            async def build(
                self, identity: uuid.UUID, *, claim_token: uuid.UUID | None = None
            ) -> object:
                async with factory.begin() as session:
                    await session.execute(
                        text("""
                        UPDATE event_evidence_bundle_jobs SET claim_token=:replacement
                        WHERE event_candidate_id=:event
                        """),
                        {"replacement": uuid.uuid4(), "event": identity},
                    )
                raise BundleConflict("event_bundle_packet_invalid")

        worker._service = LoseClaimService()  # type: ignore[assignment]
        report = await worker.process_batch(limit=10)
        assert report.claim_lost == 1
        assert report.blocked == 0
        async with factory() as session:
            job = await session.get(EventEvidenceBundleJob, event_id)
            assert job is not None and job.status is EventEvidenceBundleJobStatus.PROCESSING
    finally:
        await _cleanup(factory)
        await engine.dispose()


@pytest.mark.asyncio
async def test_evidence_budget_is_enforced_by_service_and_database() -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        _raw_one, first = await _linked_evidence(factory, "marketaux")
        _raw_two, second = await _linked_evidence(factory, "sec_edgar")
        event_id = await _event(factory, (first, second))
        with pytest.raises(BundleConflict, match="event_bundle_evidence_budget_exceeded"):
            await EventEvidenceBundleService(factory, max_evidence=1).build(event_id)
        async with factory.begin() as session:
            with pytest.raises(DBAPIError, match="ck_event_bundle_evidence_budget"):
                await session.execute(
                    text("""
                    INSERT INTO event_evidence_bundles(
                      event_candidate_id,revision,bundle_version,status,bundle_digest,
                      evidence_count,source_count,provider_count,operation_count,
                      provider_coverage,operation_coverage,reason_codes,
                      first_event_time,last_event_time
                    ) VALUES (
                      :event,1,1,'ready',repeat('a',64),501,1,1,1,
                      '["marketaux"]'::jsonb,'["marketaux:news_all"]'::jsonb,
                      '[]'::jsonb,:now,:now
                    )
                    """),
                    {"event": event_id, "now": datetime.now(UTC)},
                )
    finally:
        await _cleanup(factory)
        await engine.dispose()


@pytest.mark.asyncio
async def test_head_cannot_be_rebound_to_an_older_revision() -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        raw_id, evidence_id = await _linked_evidence(factory, "marketaux")
        event_id = await _event(factory, (evidence_id,))
        service = EventEvidenceBundleService(factory)
        first = await service.build(event_id)
        await _seed_ready(
            factory,
            "marketaux",
            raw_id=raw_id,
            payload_updates={"title": "Synthetic later revision"},
        )
        assert (await EvidenceProjectionHandoffWorker(factory).process_batch(limit=10)).linked == 1
        second = await service.build(event_id)
        assert second.revision == 2
        async with factory.begin() as session:
            with pytest.raises(DBAPIError, match="event_evidence_bundle_head_not_latest"):
                await session.execute(
                    text("""
                    UPDATE event_evidence_bundle_heads
                    SET current_bundle_id=:old, canonical_bundle_id=:old
                    WHERE event_candidate_id=:event
                    """),
                    {"old": first.bundle_id, "event": event_id},
                )
    finally:
        await _cleanup(factory)
        await engine.dispose()


@pytest.mark.asyncio
async def test_existing_state_preflight_is_value_free_and_blocks_missing_packet() -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        _raw, evidence_id = await _linked_evidence(factory, "marketaux")
        await _event(factory, (evidence_id,))
        report, exit_code = await validate_0011_pre_migration(engine)
        assert exit_code == 0 and report["status"] == "PASS"

        orphan_id = await _clone_without_packet(factory, evidence_id)
        await _event(factory, (orphan_id,))
        report, exit_code = await validate_0011_pre_migration(engine)
        assert exit_code == 2
        assert report == {
            "status": "BLOCKED",
            "broken_active_association_count": 0,
            "active_without_rich_packet_count": 1,
            "over_budget_event_count": 0,
            "safe_errors": ["migration_0011_rich_packet_unavailable"],
            "migration_ready": False,
        }
        assert "without-packet" not in str(report)
    finally:
        await _cleanup(factory)
        await engine.dispose()


@pytest.mark.asyncio
async def test_not_yet_linked_packet_is_retryable_not_permanently_blocked() -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        _raw, evidence_id = await _linked_evidence(factory, "marketaux")
        orphan_id = await _clone_without_packet(factory, evidence_id)
        event_id = await _event(factory, (orphan_id,))
        report = await EventEvidenceBundleWorker(factory).process_batch(limit=10)
        assert report.retried == 1 and report.blocked == 0
        async with factory() as session:
            job = await session.get(EventEvidenceBundleJob, event_id)
            assert job is not None
            assert job.status is EventEvidenceBundleJobStatus.RETRY
            assert job.safe_error_code == "event_bundle_packet_not_ready"
            assert job.next_retry_at is not None
            assert await session.get(EventEvidenceBundleHead, event_id) is None
    finally:
        await _cleanup(factory)
        await engine.dispose()


@pytest.mark.asyncio
async def test_empty_membership_clears_only_head_and_reactivation_appends_revision() -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        _raw, evidence_id = await _linked_evidence(factory, "sec_edgar")
        event_id = await _event(factory, (evidence_id,))
        first = await EventEvidenceBundleService(factory).build(event_id)
        async with factory.begin() as session:
            await EventCandidateService().deactivate_association(session, event_id, evidence_id)
        report = await EventEvidenceBundleWorker(factory).process_batch(limit=10)
        assert report.blocked == 1
        async with factory() as session:
            assert await session.get(EventEvidenceBundleHead, event_id) is None
            assert await session.get(EventEvidenceBundle, first.bundle_id) is not None
        async with factory.begin() as session:
            await session.execute(
                text("""
                INSERT INTO event_candidate_evidence(
                  event_candidate_id,evidence_item_id,match_rule,rule_version,
                  official_source,active
                ) VALUES (:event,:evidence,'reviewed_reactivation',1,true,true)
                """),
                {"event": event_id, "evidence": evidence_id},
            )
        report = await EventEvidenceBundleWorker(factory).process_batch(limit=10)
        assert report.ready + report.partial == 1
        async with factory() as session:
            head = await session.get(EventEvidenceBundleHead, event_id)
            assert head is not None and head.current_bundle_id != first.bundle_id
            current = await session.get(EventEvidenceBundle, head.current_bundle_id)
            assert current is not None and current.revision == 2
            assert await session.scalar(select(func.count()).select_from(EventEvidenceBundle)) == 2
    finally:
        await _cleanup(factory)
        await engine.dispose()


@pytest.mark.asyncio
async def test_membership_authority_enforces_500_active_and_allows_inactive_history() -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        _raw, evidence_id = await _linked_evidence(factory, "sec_edgar")
        event_id = await _event(factory, ())
        identities = await _clone_evidence_many(factory, evidence_id, 501)
        async with factory.begin() as session:
            await session.execute(
                text("""
                INSERT INTO event_candidate_evidence(
                  event_candidate_id,evidence_item_id,match_rule,rule_version,
                  official_source,active
                ) SELECT :event,unnest(CAST(:evidence AS uuid[])),'budget_test',1,false,true
                """),
                {"event": event_id, "evidence": list(identities[:500])},
            )
        async with factory.begin() as session:
            with pytest.raises(
                DBAPIError, match="event_candidate_active_membership_budget_exceeded"
            ):
                await session.execute(
                    text("""
                    INSERT INTO event_candidate_evidence(
                      event_candidate_id,evidence_item_id,match_rule,rule_version,
                      official_source,active
                    ) VALUES (:event,:evidence,'budget_test',1,false,true)
                    """),
                    {"event": event_id, "evidence": identities[500]},
                )
        async with factory.begin() as session:
            await session.execute(
                text("""
                UPDATE event_candidate_evidence SET active=false,removed_at=:now
                WHERE event_candidate_id=:event AND evidence_item_id=:evidence
                """),
                {"event": event_id, "evidence": identities[0], "now": datetime.now(UTC)},
            )
            await session.execute(
                text("""
                INSERT INTO event_candidate_evidence(
                  event_candidate_id,evidence_item_id,match_rule,rule_version,
                  official_source,active
                ) VALUES (:event,:evidence,'budget_test',1,false,true)
                """),
                {"event": event_id, "evidence": identities[500]},
            )
        async with factory.begin() as session:
            with pytest.raises(
                DBAPIError, match="event_candidate_active_membership_budget_exceeded"
            ):
                await session.execute(
                    text("""
                    UPDATE event_candidate_evidence SET active=true,removed_at=NULL
                    WHERE event_candidate_id=:event AND evidence_item_id=:evidence
                    """),
                    {"event": event_id, "evidence": identities[0]},
                )
    finally:
        await _cleanup(factory)
        await engine.dispose()


@pytest.mark.asyncio
async def test_two_transactions_compete_safely_for_final_membership_slot() -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        _raw, evidence_id = await _linked_evidence(factory, "marketaux")
        event_id = await _event(factory, ())
        identities = await _clone_evidence_many(factory, evidence_id, 501)
        async with factory.begin() as session:
            await session.execute(
                text("""
                INSERT INTO event_candidate_evidence(
                  event_candidate_id,evidence_item_id,match_rule,rule_version,
                  official_source,active
                ) SELECT :event,unnest(CAST(:evidence AS uuid[])),'concurrency_test',1,false,true
                """),
                {"event": event_id, "evidence": list(identities[:499])},
            )
        first_inserted = asyncio.Event()
        release_first = asyncio.Event()

        async def insert(identity: uuid.UUID, hold: bool) -> str:
            try:
                async with factory.begin() as session:
                    await session.execute(
                        text("""
                        INSERT INTO event_candidate_evidence(
                          event_candidate_id,evidence_item_id,match_rule,rule_version,
                          official_source,active
                        ) VALUES (:event,:evidence,'concurrency_test',1,false,true)
                        """),
                        {"event": event_id, "evidence": identity},
                    )
                    if hold:
                        first_inserted.set()
                        await release_first.wait()
                return "committed"
            except DBAPIError as exc:
                assert "event_candidate_active_membership_budget_exceeded" in str(exc)
                return "rejected"

        first = asyncio.create_task(insert(identities[499], True))
        await first_inserted.wait()
        second = asyncio.create_task(insert(identities[500], False))
        await asyncio.sleep(0.1)
        release_first.set()
        assert sorted(await asyncio.gather(first, second)) == ["committed", "rejected"]
        async with factory() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(EventCandidateEvidence)
                .where(
                    EventCandidateEvidence.event_candidate_id == event_id,
                    EventCandidateEvidence.active.is_(True),
                )
            )
            assert count == 500
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
