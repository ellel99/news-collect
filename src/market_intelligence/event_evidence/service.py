"""Transactional, append-only RichEvidencePacket to EventEvidenceBundle consumer."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from market_intelligence.db.models import (
    EventCandidate,
    EventCandidateEvidence,
    EventEvidenceBundle,
    EventEvidenceBundleHead,
    EventEvidenceBundleItem,
    EventEvidenceBundleJob,
    EventEvidenceBundleJobStatus,
    EventEvidenceBundleStatus,
    EventEvidenceRelation,
    EvidenceProjectionLink,
    SafeFactProjection,
)
from market_intelligence.event_evidence.contracts import (
    BundleBuildResult,
    BundleClaimLost,
    BundleConflict,
    BundleRetryableConflict,
)
from market_intelligence.rich_evidence.builder import RichEvidencePacketBuilder
from market_intelligence.rich_evidence.contracts import RichEvidenceError, RichEvidencePacket

_RELATION_RULE = "m2c_relation_v1"
_RULE_VERSION = 1


@dataclasses.dataclass(frozen=True, slots=True)
class _Prepared:
    association_id: uuid.UUID
    evidence_id: uuid.UUID
    packet: RichEvidencePacket
    fact_identity_digest: str
    fact_value_digest: str


class EventEvidenceBundleService:
    """Create one immutable revision for an EventCandidate when its material changes."""

    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        *,
        packet_builder: RichEvidencePacketBuilder | None = None,
        max_evidence: int = 500,
    ) -> None:
        if not 1 <= max_evidence <= 500:
            raise ValueError("event_bundle_evidence_budget_invalid")
        self._factory = factory
        self._builder = packet_builder or RichEvidencePacketBuilder(
            factory, max_batch_size=max_evidence
        )
        self._max_evidence = max_evidence

    async def build(
        self, event_candidate_id: uuid.UUID, *, claim_token: uuid.UUID | None = None
    ) -> BundleBuildResult:
        associations = await self._active_associations(event_candidate_id)
        if not associations:
            await self._clear_head_for_empty_membership(event_candidate_id, claim_token)
            raise BundleConflict("event_bundle_no_active_evidence")
        if len(associations) > self._max_evidence:
            raise BundleConflict("event_bundle_evidence_budget_exceeded")
        prepared: list[_Prepared] = []
        try:
            packets = await self._builder.build_many(
                tuple(evidence_id for _association_id, evidence_id in associations)
            )
        except RichEvidenceError as exc:
            if str(exc) == "rich_evidence_linked_projection_missing":
                raise BundleRetryableConflict("event_bundle_packet_not_ready") from exc
            raise BundleConflict("event_bundle_packet_invalid") from exc
        for (association_id, evidence_id), packet in zip(associations, packets, strict=True):
            try:
                identity, value = _fact_digests(packet)
            except BundleConflict:
                raise
            prepared.append(_Prepared(association_id, evidence_id, packet, identity, value))

        async with self._factory.begin() as session:
            if claim_token is not None:
                job = await session.get(
                    EventEvidenceBundleJob, event_candidate_id, with_for_update=True
                )
                if (
                    job is None
                    or job.status is not EventEvidenceBundleJobStatus.PROCESSING
                    or job.claim_token != claim_token
                ):
                    raise BundleClaimLost("event_bundle_claim_lost")
            candidate = await session.scalar(
                select(EventCandidate)
                .where(EventCandidate.id == event_candidate_id)
                .with_for_update()
            )
            if candidate is None:
                raise BundleConflict("event_bundle_candidate_missing")
            locked = tuple(
                (
                    row.id,
                    row.evidence_item_id,
                )
                for row in await session.scalars(
                    select(EventCandidateEvidence)
                    .where(
                        EventCandidateEvidence.event_candidate_id == event_candidate_id,
                        EventCandidateEvidence.active.is_(True),
                    )
                    .order_by(EventCandidateEvidence.id)
                    .with_for_update()
                )
            )
            if locked != associations:
                raise BundleRetryableConflict("event_bundle_membership_changed")
            if not await _packets_still_current(session, prepared):
                raise BundleRetryableConflict("event_bundle_packet_snapshot_changed")
            head = await session.get(
                EventEvidenceBundleHead, event_candidate_id, with_for_update=True
            )
            previous_items = await _previous_items(session, head)
            if head is not None and _same_material(prepared, previous_items):
                current = await session.get(EventEvidenceBundle, head.current_bundle_id)
                if current is None:
                    raise BundleConflict("event_bundle_head_invalid")
                return BundleBuildResult(
                    "unchanged", event_candidate_id, current.id, current.revision
                )
            rows = _classify(prepared, previous_items)
            status, reasons = _quality(prepared)
            material = _bundle_material(event_candidate_id, status.value, reasons, rows)
            digest = _digest(material)
            if head is not None:
                current = await session.get(EventEvidenceBundle, head.current_bundle_id)
                if current is not None and current.bundle_digest == digest:
                    return BundleBuildResult(
                        "unchanged", event_candidate_id, current.id, current.revision
                    )
            maximum_revision = await session.scalar(
                select(func.coalesce(func.max(EventEvidenceBundle.revision), 0)).where(
                    EventEvidenceBundle.event_candidate_id == event_candidate_id
                )
            )
            revision = (maximum_revision or 0) + 1
            event_times = [row.packet.current_published_at for row in prepared]
            providers = sorted({row.packet.provider for row in prepared})
            operations = sorted(
                {f"{row.packet.provider}:{row.packet.operation_key}" for row in prepared}
            )
            sources = {row.packet.provenance.source_id for row in prepared}
            bundle = EventEvidenceBundle(
                event_candidate_id=event_candidate_id,
                revision=revision,
                bundle_version=1,
                status=status,
                bundle_digest=digest,
                evidence_count=len(rows),
                source_count=len(sources),
                provider_count=len(providers),
                operation_count=len(operations),
                provider_coverage=providers,
                operation_coverage=operations,
                reason_codes=list(reasons),
                first_event_time=min(event_times),
                last_event_time=max(event_times),
            )
            session.add(bundle)
            await session.flush()
            for row, relation in rows:
                session.add(
                    EventEvidenceBundleItem(
                        bundle_id=bundle.id,
                        event_candidate_evidence_id=row.association_id,
                        evidence_item_id=row.evidence_id,
                        packet_digest=row.packet.packet_digest,
                        projection_hash=row.packet.current.projection_hash,
                        fact_identity_digest=row.fact_identity_digest,
                        fact_value_digest=row.fact_value_digest,
                        relation=relation,
                        relation_rule=_RELATION_RULE,
                        rule_version=_RULE_VERSION,
                        provider=row.packet.provider,
                        operation_key=row.packet.operation_key,
                        source_id=row.packet.provenance.source_id,
                        event_time=row.packet.current_published_at,
                    )
                )
            if head is None:
                head = EventEvidenceBundleHead(
                    event_candidate_id=event_candidate_id,
                    current_bundle_id=bundle.id,
                    canonical_bundle_id=(
                        bundle.id if status is EventEvidenceBundleStatus.READY else None
                    ),
                )
                session.add(head)
            else:
                head.current_bundle_id = bundle.id
                if status is EventEvidenceBundleStatus.READY:
                    head.canonical_bundle_id = bundle.id
                head.updated_at = datetime.now(UTC)
            return BundleBuildResult(status.value, event_candidate_id, bundle.id, revision)

    async def dependency_fingerprint(self, event_candidate_id: uuid.UUID) -> str:
        """Return a value-free watermark for membership and linked packet dependencies."""
        async with self._factory() as session:
            rows = (
                await session.execute(
                    select(
                        EventCandidateEvidence.id,
                        EventCandidateEvidence.evidence_item_id,
                        EventCandidateEvidence.active,
                        EventCandidateEvidence.removed_at,
                        EvidenceProjectionLink.id,
                        EvidenceProjectionLink.status,
                        EvidenceProjectionLink.updated_at,
                        SafeFactProjection.id,
                        SafeFactProjection.projection_hash,
                        SafeFactProjection.processing_status,
                    )
                    .outerjoin(
                        EvidenceProjectionLink,
                        EvidenceProjectionLink.evidence_item_id
                        == EventCandidateEvidence.evidence_item_id,
                    )
                    .outerjoin(
                        SafeFactProjection,
                        SafeFactProjection.id == EvidenceProjectionLink.safe_fact_projection_id,
                    )
                    .where(EventCandidateEvidence.event_candidate_id == event_candidate_id)
                    .order_by(
                        EventCandidateEvidence.id,
                        EvidenceProjectionLink.id,
                        SafeFactProjection.id,
                    )
                )
            ).all()
        return _digest(
            [[str(value) if value is not None else None for value in row] for row in rows]
        )

    async def _clear_head_for_empty_membership(
        self, event_candidate_id: uuid.UUID, claim_token: uuid.UUID | None
    ) -> None:
        async with self._factory.begin() as session:
            if claim_token is not None:
                job = await session.get(
                    EventEvidenceBundleJob, event_candidate_id, with_for_update=True
                )
                if (
                    job is None
                    or job.status is not EventEvidenceBundleJobStatus.PROCESSING
                    or job.claim_token != claim_token
                ):
                    raise BundleClaimLost("event_bundle_claim_lost")
            candidate = await session.scalar(
                select(EventCandidate)
                .where(EventCandidate.id == event_candidate_id)
                .with_for_update()
            )
            if candidate is None:
                raise BundleConflict("event_bundle_candidate_missing")
            active = await session.scalar(
                select(EventCandidateEvidence.id)
                .where(
                    EventCandidateEvidence.event_candidate_id == event_candidate_id,
                    EventCandidateEvidence.active.is_(True),
                )
                .limit(1)
                .with_for_update()
            )
            if active is not None:
                raise BundleRetryableConflict("event_bundle_membership_changed")
            head = await session.get(
                EventEvidenceBundleHead, event_candidate_id, with_for_update=True
            )
            if head is not None:
                await session.delete(head)

    async def _active_associations(
        self, event_candidate_id: uuid.UUID
    ) -> tuple[tuple[uuid.UUID, uuid.UUID], ...]:
        async with self._factory() as session:
            return tuple(
                (row.id, row.evidence_item_id)
                for row in await session.scalars(
                    select(EventCandidateEvidence)
                    .where(
                        EventCandidateEvidence.event_candidate_id == event_candidate_id,
                        EventCandidateEvidence.active.is_(True),
                    )
                    .order_by(EventCandidateEvidence.id)
                    .limit(self._max_evidence + 1)
                )
            )


async def _previous_items(
    session: AsyncSession, head: EventEvidenceBundleHead | None
) -> dict[uuid.UUID, EventEvidenceBundleItem]:
    if head is None:
        return {}
    return {
        row.event_candidate_evidence_id: row
        for row in await session.scalars(
            select(EventEvidenceBundleItem).where(
                EventEvidenceBundleItem.bundle_id == head.current_bundle_id
            )
        )
    }


def _same_material(
    prepared: list[_Prepared], previous: dict[uuid.UUID, EventEvidenceBundleItem]
) -> bool:
    if {row.association_id for row in prepared} != set(previous):
        return False
    return all(
        previous[row.association_id].evidence_item_id == row.evidence_id
        and previous[row.association_id].packet_digest == row.packet.packet_digest
        and previous[row.association_id].projection_hash == row.packet.current.projection_hash
        and previous[row.association_id].fact_identity_digest == row.fact_identity_digest
        and previous[row.association_id].fact_value_digest == row.fact_value_digest
        for row in prepared
    )


def _classify(
    prepared: list[_Prepared], previous: dict[uuid.UUID, EventEvidenceBundleItem]
) -> list[tuple[_Prepared, EventEvidenceRelation]]:
    grouped: dict[str, list[_Prepared]] = defaultdict(list)
    for row in prepared:
        grouped[row.fact_identity_digest].append(row)
    result: list[tuple[_Prepared, EventEvidenceRelation]] = []
    for identity in sorted(grouped):
        value_groups: dict[str, list[_Prepared]] = defaultdict(list)
        for row in grouped[identity]:
            value_groups[row.fact_value_digest].append(row)
        for value_digest in sorted(value_groups):
            rows = sorted(
                value_groups[value_digest],
                key=lambda row: (row.packet.packet_digest, row.evidence_id.hex),
            )
            for row in rows:
                prior = previous.get(row.association_id)
                if prior is not None and prior.packet_digest != row.packet.packet_digest:
                    relation = EventEvidenceRelation.SUPERSEDING
                elif len(rows) > 1:
                    relation = EventEvidenceRelation.DUPLICATE
                elif len(value_groups) > 1:
                    relation = EventEvidenceRelation.CONTRADICTING
                else:
                    relation = EventEvidenceRelation.SUPPORTING
                result.append((row, relation))
    return sorted(result, key=lambda item: item[0].association_id.hex)


async def _packets_still_current(session: AsyncSession, prepared: list[_Prepared]) -> bool:
    """Validate every packet revision against the state visible in the commit transaction."""
    evidence_ids = tuple(row.evidence_id for row in prepared)
    rows = (
        await session.execute(
            select(
                EvidenceProjectionLink.evidence_item_id,
                EvidenceProjectionLink.id,
                SafeFactProjection.id,
                SafeFactProjection.projection_hash,
            )
            .join(
                SafeFactProjection,
                SafeFactProjection.id == EvidenceProjectionLink.safe_fact_projection_id,
            )
            .where(
                EvidenceProjectionLink.evidence_item_id.in_(evidence_ids),
                EvidenceProjectionLink.status == "linked",
                SafeFactProjection.processing_status == "ready",
            )
        )
    ).all()
    actual: dict[uuid.UUID, set[tuple[uuid.UUID, uuid.UUID, str]]] = defaultdict(set)
    for evidence_id, link_id, projection_id, projection_hash in rows:
        actual[evidence_id].add((link_id, projection_id, projection_hash))
    for row in prepared:
        current = (
            row.packet.current.link_id,
            row.packet.current.projection_id,
            row.packet.current.projection_hash,
        )
        observed = actual.get(row.evidence_id, set())
        if len(observed) != row.packet.truncation.total_revision_count or current not in observed:
            return False
    return True


def _quality(
    prepared: list[_Prepared],
) -> tuple[EventEvidenceBundleStatus, tuple[str, ...]]:
    reasons: set[str] = set()
    for row in prepared:
        if row.packet.quality == "partial":
            reasons.add("event_bundle_partial_packet")
        if row.packet.truncation.truncated:
            reasons.add("event_bundle_packet_truncated")
    status = EventEvidenceBundleStatus.PARTIAL if reasons else EventEvidenceBundleStatus.READY
    return status, tuple(sorted(reasons))


def _fact_digests(packet: RichEvidencePacket) -> tuple[str, str]:
    facts = dataclasses.asdict(packet.current.facts)
    identity_fields: dict[tuple[str, str], tuple[str, ...]] = {
        ("marketaux", "news_all"): ("provider_item_id",),
        ("finnhub", "quote"): ("symbol", "provider_timestamp"),
        ("finnhub", "company_news"): ("provider_item_id",),
        ("eia", "electricity_retail_sales"): ("series_identity", "period"),
        ("eia", "electricity_rto_region_data"): ("series_identity", "period"),
        ("sec_edgar", "submissions_recent"): ("accession_number",),
    }
    fields = identity_fields.get((packet.provider, packet.operation_key))
    if fields is None or any(not facts.get(field) for field in fields):
        raise BundleConflict("event_bundle_fact_identity_invalid")
    identity = {
        "provider": packet.provider,
        "operation_key": packet.operation_key,
        "identity": {field: facts[field] for field in fields},
    }
    value = {
        "provider": packet.provider,
        "operation_key": packet.operation_key,
        "facts": facts,
    }
    return _digest(identity), _digest(value)


def _bundle_material(
    event_candidate_id: uuid.UUID,
    status: str,
    reasons: tuple[str, ...],
    rows: list[tuple[_Prepared, EventEvidenceRelation]],
) -> dict[str, Any]:
    return {
        "bundle_version": 1,
        "event_candidate_id": str(event_candidate_id),
        "status": status,
        "reason_codes": list(reasons),
        "items": [
            {
                "association_id": str(row.association_id),
                "evidence_id": str(row.evidence_id),
                "packet_digest": row.packet.packet_digest,
                "projection_hash": row.packet.current.projection_hash,
                "fact_identity_digest": row.fact_identity_digest,
                "fact_value_digest": row.fact_value_digest,
                "relation": relation.value,
                "provider": row.packet.provider,
                "operation_key": row.packet.operation_key,
                "source_id": str(row.packet.provenance.source_id),
                "event_time": row.packet.current_published_at.astimezone(UTC).isoformat(),
            }
            for row, relation in rows
        ],
    }


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode()).hexdigest()
