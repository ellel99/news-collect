"""Bounded Event processing and impact-analysis runtime."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from market_intelligence.event_intelligence.analysis import (
    ImpactAnalyzer,
    ImpactRequest,
    validate_impact_analysis,
)
from market_intelligence.event_intelligence.analyzers import AnalyzerRuntimeError
from market_intelligence.event_intelligence.fact_layer import FactLayerBuilder, FactSnapshot
from market_intelligence.event_intelligence.persistence import (
    AnalyzerIdentity,
    ImpactAnalysisStore,
    ImpactPersistenceOutcome,
)
from market_intelligence.event_intelligence.service import EventCandidateService


class EventRuntimeStatus(StrEnum):
    PASS = "PASS"
    NO_CHANGE = "NO_CHANGE"
    RETRY = "RETRY"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True, slots=True)
class EventRuntimeOutcome:
    status: EventRuntimeStatus
    evidence_item_id: UUID
    event_candidate_id: UUID | None
    fact_snapshot_hash: str | None
    analysis_id: UUID | None
    analysis_version: int | None
    safe_errors: tuple[str, ...] = ()


class EventProcessingRuntime:
    def __init__(
        self,
        *,
        candidates: EventCandidateService | None = None,
        facts: FactLayerBuilder | None = None,
        analyses: ImpactAnalysisStore | None = None,
    ) -> None:
        self._candidates = candidates or EventCandidateService()
        self._facts = facts or FactLayerBuilder()
        self._analyses = analyses or ImpactAnalysisStore()

    async def process_evidence(
        self,
        session: AsyncSession,
        evidence_item_id: UUID,
        *,
        analyzer: ImpactAnalyzer | None = None,
        analyzer_identity: AnalyzerIdentity | None = None,
    ) -> EventRuntimeOutcome:
        try:
            async with session.begin_nested():
                candidate = await self._candidates.process(session, evidence_item_id)
                fact = await self._facts.build(session, candidate.event_candidate_id)
        except ValueError as error:
            return EventRuntimeOutcome(
                EventRuntimeStatus.BLOCKED,
                evidence_item_id,
                None,
                None,
                None,
                None,
                (str(error),),
            )
        if analyzer is None:
            return EventRuntimeOutcome(
                EventRuntimeStatus.NO_CHANGE
                if candidate.status in {"existing", "concurrent_existing"}
                else EventRuntimeStatus.PASS,
                evidence_item_id,
                candidate.event_candidate_id,
                fact.snapshot_hash,
                None,
                None,
            )
        if analyzer_identity is None:
            return EventRuntimeOutcome(
                EventRuntimeStatus.BLOCKED,
                evidence_item_id,
                candidate.event_candidate_id,
                fact.snapshot_hash,
                None,
                None,
                ("analyzer_identity_missing",),
            )
        persistence = await self._run_analysis(
            session, evidence_item_id, fact, analyzer, analyzer_identity
        )
        return EventRuntimeOutcome(
            _runtime_status(persistence.status),
            evidence_item_id,
            candidate.event_candidate_id,
            fact.snapshot_hash,
            persistence.analysis_id,
            persistence.analysis_version,
            persistence.safe_errors,
        )

    async def _run_analysis(
        self,
        session: AsyncSession,
        evidence_item_id: UUID,
        fact: FactSnapshot,
        analyzer: ImpactAnalyzer,
        identity: AnalyzerIdentity,
    ) -> ImpactPersistenceOutcome:
        del evidence_item_id
        request = ImpactRequest(
            event_candidate_id=fact.event_candidate_id,
            fact_summary=fact.what_happened,
            evidence_count=fact.evidence_count,
            source_count=fact.source_count,
            official_evidence_present=fact.official_evidence_present,
            affected_entity_refs=fact.primary_entities,
            affected_asset_refs=fact.assets,
            affected_sector_refs=fact.sectors,
            uncertainty=fact.uncertainty + fact.contradictions,
        )
        try:
            analysis = await analyzer.analyze(request)
        except AnalyzerRuntimeError as error:
            return await self._analyses.record_failure(
                session,
                fact,
                identity,
                retryable=error.retryable,
                safe_error=error.code,
            )
        errors = validate_impact_analysis(analysis)
        if errors:
            return await self._analyses.record_failure(
                session,
                fact,
                identity,
                retryable=False,
                safe_error=errors[0],
            )
        return await self._analyses.persist_valid(session, fact, identity, analysis)


def _runtime_status(value: str) -> EventRuntimeStatus:
    return {
        "written": EventRuntimeStatus.PASS,
        "existing": EventRuntimeStatus.NO_CHANGE,
        "retry": EventRuntimeStatus.RETRY,
        "failed": EventRuntimeStatus.FAILED,
        "invalid": EventRuntimeStatus.FAILED,
    }.get(value, EventRuntimeStatus.FAILED)
