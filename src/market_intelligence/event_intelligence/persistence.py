"""Versioned and idempotent ImpactAnalysis persistence."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from market_intelligence.db.models import ImpactAnalysisRecord, ImpactAnalysisStatus
from market_intelligence.event_intelligence.analysis import ImpactAnalysis, validate_impact_analysis
from market_intelligence.event_intelligence.fact_layer import FactSnapshot


@dataclass(frozen=True, slots=True)
class AnalyzerIdentity:
    provider: str
    model: str
    contract_version: int = 1


@dataclass(frozen=True, slots=True)
class ImpactPersistenceOutcome:
    status: str
    analysis_id: UUID | None
    analysis_version: int | None
    safe_errors: tuple[str, ...] = ()


class ImpactAnalysisStore:
    async def record_failure(
        self,
        session: AsyncSession,
        fact: FactSnapshot,
        identity: AnalyzerIdentity,
        *,
        retryable: bool,
        safe_error: str,
    ) -> ImpactPersistenceOutcome:
        existing = await self._existing(session, fact, identity)
        if existing is not None:
            if existing.status is ImpactAnalysisStatus.VALID:
                return ImpactPersistenceOutcome("existing", existing.id, existing.analysis_version)
            existing.status = (
                ImpactAnalysisStatus.RETRY if retryable else ImpactAnalysisStatus.FAILED
            )
            existing.safe_errors = [safe_error]
            await session.flush()
            return ImpactPersistenceOutcome(
                existing.status.value, existing.id, existing.analysis_version, (safe_error,)
            )
        latest, next_version = await self._latest_and_next(session, fact.event_candidate_id)
        row = ImpactAnalysisRecord(
            event_candidate_id=fact.event_candidate_id,
            analysis_version=next_version,
            fact_version=fact.fact_version,
            fact_snapshot_hash=fact.snapshot_hash,
            analyzer_provider=identity.provider,
            analyzer_model=identity.model,
            analyzer_contract_version=identity.contract_version,
            affected_companies=[],
            affected_assets=[],
            affected_sectors=[],
            impact_direction="uncertain",
            impact_horizon="short_term",
            impact_channels=[],
            confidence=0,
            rationale_summary="Analysis unavailable; safe retry or review required.",
            uncertainty=["analysis_unavailable"],
            required_market_validation=True,
            status=ImpactAnalysisStatus.RETRY if retryable else ImpactAnalysisStatus.FAILED,
            supersedes_analysis_id=latest.id if latest is not None else None,
            safe_errors=[safe_error],
        )
        session.add(row)
        await session.flush()
        return ImpactPersistenceOutcome(
            row.status.value, row.id, row.analysis_version, (safe_error,)
        )

    async def persist_valid(
        self,
        session: AsyncSession,
        fact: FactSnapshot,
        identity: AnalyzerIdentity,
        analysis: ImpactAnalysis,
    ) -> ImpactPersistenceOutcome:
        errors = validate_impact_analysis(analysis)
        if errors:
            return ImpactPersistenceOutcome("invalid", None, None, errors)
        existing = await self._existing(session, fact, identity)
        if existing is not None and existing.status is ImpactAnalysisStatus.VALID:
            return ImpactPersistenceOutcome("existing", existing.id, existing.analysis_version)
        if existing is not None:
            _apply_valid(existing, analysis)
            existing.status = ImpactAnalysisStatus.VALID
            existing.safe_errors = []
            await session.flush()
            return ImpactPersistenceOutcome("written", existing.id, existing.analysis_version)
        latest, next_version = await self._latest_and_next(session, fact.event_candidate_id)
        row = ImpactAnalysisRecord(
            event_candidate_id=fact.event_candidate_id,
            analysis_version=next_version,
            fact_version=fact.fact_version,
            fact_snapshot_hash=fact.snapshot_hash,
            analyzer_provider=identity.provider,
            analyzer_model=identity.model,
            analyzer_contract_version=identity.contract_version,
            affected_companies=list(analysis.affected_companies),
            affected_assets=list(analysis.affected_assets),
            affected_sectors=list(analysis.affected_sectors),
            impact_direction=analysis.impact_direction.value,
            impact_horizon=analysis.impact_horizon.value,
            impact_channels=list(analysis.impact_channels),
            confidence=analysis.confidence,
            rationale_summary=analysis.rationale_summary,
            uncertainty=list(analysis.uncertainty),
            required_market_validation=analysis.required_market_validation,
            status=ImpactAnalysisStatus.VALID,
            supersedes_analysis_id=latest.id if latest is not None else None,
            safe_errors=[],
        )
        try:
            async with session.begin_nested():
                session.add(row)
                await session.flush()
        except IntegrityError:
            existing = await self._existing(session, fact, identity)
            if existing is None:
                return ImpactPersistenceOutcome(
                    "failed", None, None, ("impact_analysis_persistence_conflict",)
                )
            return ImpactPersistenceOutcome("existing", existing.id, existing.analysis_version)
        return ImpactPersistenceOutcome("written", row.id, row.analysis_version)

    async def _latest_and_next(
        self, session: AsyncSession, event_candidate_id: UUID
    ) -> tuple[ImpactAnalysisRecord | None, int]:
        latest = await session.scalar(
            select(ImpactAnalysisRecord)
            .where(ImpactAnalysisRecord.event_candidate_id == event_candidate_id)
            .order_by(ImpactAnalysisRecord.analysis_version.desc())
            .limit(1)
        )
        next_version = (
            int(
                await session.scalar(
                    select(func.coalesce(func.max(ImpactAnalysisRecord.analysis_version), 0)).where(
                        ImpactAnalysisRecord.event_candidate_id == event_candidate_id
                    )
                )
                or 0
            )
            + 1
        )
        return latest, next_version

    async def _existing(
        self, session: AsyncSession, fact: FactSnapshot, identity: AnalyzerIdentity
    ) -> ImpactAnalysisRecord | None:
        existing: ImpactAnalysisRecord | None = await session.scalar(
            select(ImpactAnalysisRecord).where(
                ImpactAnalysisRecord.event_candidate_id == fact.event_candidate_id,
                ImpactAnalysisRecord.fact_snapshot_hash == fact.snapshot_hash,
                ImpactAnalysisRecord.analyzer_provider == identity.provider,
                ImpactAnalysisRecord.analyzer_model == identity.model,
                ImpactAnalysisRecord.analyzer_contract_version == identity.contract_version,
            )
        )
        return existing


def _apply_valid(row: ImpactAnalysisRecord, analysis: ImpactAnalysis) -> None:
    row.affected_companies = list(analysis.affected_companies)
    row.affected_assets = list(analysis.affected_assets)
    row.affected_sectors = list(analysis.affected_sectors)
    row.impact_direction = analysis.impact_direction.value
    row.impact_horizon = analysis.impact_horizon.value
    row.impact_channels = list(analysis.impact_channels)
    row.confidence = analysis.confidence
    row.rationale_summary = analysis.rationale_summary
    row.uncertainty = list(analysis.uncertainty)
    row.required_market_validation = analysis.required_market_validation
