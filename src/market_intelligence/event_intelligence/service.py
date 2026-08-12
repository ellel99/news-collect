"""Transactional EventCandidate repository and orchestration service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from market_intelligence.db.models import (
    ContentItem,
    EventCandidate,
    EventCandidateEvidence,
    EventCandidateStatus,
    EvidenceItem,
)
from market_intelligence.event_intelligence.matching import (
    CandidateProjection,
    EvidenceProjection,
    MatchDecision,
    MatchRule,
    match_existing,
)
from market_intelligence.event_intelligence.scoring import (
    DeterministicImportanceScorer,
    ImportanceInput,
    ImportanceScorer,
)


@dataclass(frozen=True, slots=True)
class EventCandidateOutcome:
    event_candidate_id: UUID
    cluster_key: str
    status: str
    match_rule: MatchRule
    rule_version: int
    association_active: bool


class EventCandidateRepository:
    async def load_evidence_projection(
        self, session: AsyncSession, evidence_item_id: UUID
    ) -> EvidenceProjection | None:
        row = (
            await session.execute(
                select(EvidenceItem, ContentItem)
                .outerjoin(ContentItem, ContentItem.id == EvidenceItem.content_item_id)
                .where(EvidenceItem.id == evidence_item_id)
            )
        ).one_or_none()
        if row is None:
            return None
        evidence, content = row
        return EvidenceProjection(
            evidence_item_id=evidence.id,
            provider=evidence.provider,
            provider_item_id=evidence.provider_item_id,
            provider_item_hash=evidence.provider_item_hash,
            official_source=evidence.official_source_flag,
            canonical_url=content.canonical_url if content else None,
            title=content.title if content else None,
            event_time=evidence.event_time,
            observed_at=evidence.observed_at,
            entity_refs=tuple(str(value) for value in evidence.entity_refs),
            asset_refs=tuple(str(value) for value in evidence.asset_refs),
            topic_refs=tuple(str(value) for value in evidence.topic_refs),
        )

    async def load_candidates(self, session: AsyncSession) -> tuple[CandidateProjection, ...]:
        values = (await session.scalars(select(EventCandidate))).all()
        return tuple(
            CandidateProjection(
                id=value.id,
                cluster_key=value.cluster_key,
                strong_identity_hash=value.strong_identity_hash,
                identity_signatures=tuple(str(item) for item in value.identity_signatures),
                title_fingerprints=tuple(str(item) for item in value.title_fingerprints),
                first_seen_at=value.first_seen_at,
                latest_seen_at=value.latest_seen_at,
                entities=tuple(str(item) for item in value.entities),
            )
            for value in values
        )

    async def active_association(
        self, session: AsyncSession, evidence_item_id: UUID
    ) -> EventCandidateEvidence | None:
        return cast(
            EventCandidateEvidence | None,
            await session.scalar(
                select(EventCandidateEvidence)
                .where(
                    EventCandidateEvidence.evidence_item_id == evidence_item_id,
                    EventCandidateEvidence.active.is_(True),
                )
                .order_by(EventCandidateEvidence.added_at)
                .limit(1)
            ),
        )

    async def get_candidate(
        self, session: AsyncSession, candidate_id: UUID
    ) -> EventCandidate | None:
        return await session.get(EventCandidate, candidate_id)

    async def get_by_cluster_key(
        self, session: AsyncSession, cluster_key: str
    ) -> EventCandidate | None:
        return cast(
            EventCandidate | None,
            await session.scalar(
                select(EventCandidate).where(EventCandidate.cluster_key == cluster_key)
            ),
        )


class EventCandidateService:
    def __init__(
        self,
        *,
        repository: EventCandidateRepository | None = None,
        importance_scorer: ImportanceScorer | None = None,
    ) -> None:
        self.repository = repository or EventCandidateRepository()
        self.importance_scorer = importance_scorer or DeterministicImportanceScorer()

    async def process(self, session: AsyncSession, evidence_item_id: UUID) -> EventCandidateOutcome:
        projection = await self.repository.load_evidence_projection(session, evidence_item_id)
        if projection is None:
            raise ValueError("evidence_item_not_found")
        existing_link = await self.repository.active_association(session, evidence_item_id)
        if existing_link is not None:
            existing_candidate = await self.repository.get_candidate(
                session, existing_link.event_candidate_id
            )
            if existing_candidate is None:
                raise ValueError("event_candidate_not_found")
            return self._outcome(existing_candidate, existing_link, "existing")

        decision = match_existing(projection, await self.repository.load_candidates(session))
        candidate: EventCandidate | None = None
        status = "matched"
        if decision.candidate_id is not None:
            candidate = await self.repository.get_candidate(session, decision.candidate_id)
        else:
            candidate, status = await self._create_or_get(session, projection, decision)
        if candidate is None:
            raise ValueError("event_candidate_not_found")
        link = await self._associate(session, candidate, projection, decision)
        await self._refresh_candidate(session, candidate, projection, decision)
        return self._outcome(candidate, link, status)

    async def _create_or_get(
        self,
        session: AsyncSession,
        projection: EvidenceProjection,
        decision: MatchDecision,
    ) -> tuple[EventCandidate, str]:
        observed = projection.event_time or projection.observed_at
        importance = self.importance_scorer.score(
            ImportanceInput(
                official_source=projection.official_source,
                evidence_count=1,
                source_count=1,
                source_priority=projection.source_priority,
                entity_count=len(projection.entity_refs),
                corroborated=False,
            )
        )
        candidate = EventCandidate(
            cluster_key=decision.anchor.cluster_key,
            anchor_type=decision.anchor.kind,
            anchor_value_hash=decision.anchor.value_hash,
            strong_identity_hash=decision.strong_identity_hash,
            identity_signatures=list(decision.signatures),
            title_fingerprints=([decision.title_fingerprint] if decision.title_fingerprint else []),
            event_type="information_event",
            status=EventCandidateStatus.CANDIDATE,
            canonical_title=None,
            fact_summary=None,
            first_seen_at=observed,
            latest_seen_at=observed,
            occurred_at=projection.event_time,
            published_at=projection.event_time,
            primary_entity=projection.entity_refs[0] if projection.entity_refs else None,
            entities=list(projection.entity_refs),
            companies=list(projection.entity_refs),
            assets=list(projection.asset_refs),
            sectors=[],
            topics=list(projection.topic_refs),
            evidence_count=1,
            source_count=1,
            confidence=0.45 + (0.2 if projection.official_source else 0.0),
            importance_score=importance.score,
            importance_reasons=list(importance.component_reasons),
        )
        try:
            async with session.begin_nested():
                session.add(candidate)
                await session.flush()
            return candidate, "created"
        except IntegrityError:
            existing = await self.repository.get_by_cluster_key(
                session, decision.anchor.cluster_key
            )
            if existing is None:
                raise
            return existing, "concurrent_existing"

    async def _associate(
        self,
        session: AsyncSession,
        candidate: EventCandidate,
        projection: EvidenceProjection,
        decision: MatchDecision,
    ) -> EventCandidateEvidence:
        link = await session.get(
            EventCandidateEvidence, (candidate.id, projection.evidence_item_id)
        )
        if link is None:
            link = EventCandidateEvidence(
                event_candidate_id=candidate.id,
                evidence_item_id=projection.evidence_item_id,
                match_rule=decision.match_rule.value,
                rule_version=decision.rule_version,
                official_source=projection.official_source,
                active=True,
                removed_at=None,
            )
            session.add(link)
        elif not link.active:
            link.active = True
            link.removed_at = None
            link.match_rule = decision.match_rule.value
            link.rule_version = decision.rule_version
        await session.flush()
        return link

    async def deactivate_association(
        self,
        session: AsyncSession,
        event_candidate_id: UUID,
        evidence_item_id: UUID,
        *,
        removed_at: datetime | None = None,
    ) -> EventCandidateEvidence:
        link = await session.get(EventCandidateEvidence, (event_candidate_id, evidence_item_id))
        if link is None:
            raise ValueError("event_candidate_evidence_not_found")
        link.active = False
        link.removed_at = removed_at or datetime.now(UTC)
        await session.flush()
        return link

    async def _refresh_candidate(
        self,
        session: AsyncSession,
        candidate: EventCandidate,
        projection: EvidenceProjection,
        decision: MatchDecision,
    ) -> None:
        candidate.identity_signatures = sorted(
            set(str(item) for item in candidate.identity_signatures) | set(decision.signatures)
        )
        if decision.title_fingerprint:
            candidate.title_fingerprints = sorted(
                set(str(item) for item in candidate.title_fingerprints)
                | {decision.title_fingerprint}
            )
        if candidate.strong_identity_hash is None and decision.strong_identity_hash:
            candidate.strong_identity_hash = decision.strong_identity_hash
        candidate.entities = sorted(
            set(str(item) for item in candidate.entities) | set(projection.entity_refs)
        )
        candidate.assets = sorted(
            set(str(item) for item in candidate.assets) | set(projection.asset_refs)
        )
        candidate.topics = sorted(
            set(str(item) for item in candidate.topics) | set(projection.topic_refs)
        )
        observed = projection.event_time or projection.observed_at
        candidate.first_seen_at = min(candidate.first_seen_at, observed)
        candidate.latest_seen_at = max(candidate.latest_seen_at, observed)
        counts = (
            await session.execute(
                select(
                    func.count(EventCandidateEvidence.evidence_item_id),
                    func.count(func.distinct(EvidenceItem.provider)),
                    func.bool_or(EventCandidateEvidence.official_source),
                )
                .join(
                    EvidenceItem,
                    EvidenceItem.id == EventCandidateEvidence.evidence_item_id,
                )
                .where(
                    EventCandidateEvidence.event_candidate_id == candidate.id,
                    EventCandidateEvidence.active.is_(True),
                )
            )
        ).one()
        candidate.evidence_count = int(counts[0])
        candidate.source_count = int(counts[1])
        importance = self.importance_scorer.score(
            ImportanceInput(
                official_source=bool(counts[2]),
                evidence_count=candidate.evidence_count,
                source_count=candidate.source_count,
                source_priority=projection.source_priority,
                entity_count=len(candidate.entities),
                corroborated=candidate.source_count > 1,
            )
        )
        candidate.importance_score = importance.score
        candidate.importance_reasons = list(importance.component_reasons)
        candidate.confidence = min(
            1.0,
            0.45 + (0.2 if counts[2] else 0.0) + min(0.3, (candidate.source_count - 1) * 0.15),
        )
        await session.flush()

    @staticmethod
    def _outcome(
        candidate: EventCandidate, link: EventCandidateEvidence, status: str
    ) -> EventCandidateOutcome:
        return EventCandidateOutcome(
            event_candidate_id=candidate.id,
            cluster_key=candidate.cluster_key,
            status=status,
            match_rule=MatchRule(link.match_rule),
            rule_version=link.rule_version,
            association_active=link.active,
        )
