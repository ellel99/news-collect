"""IO-free ImpactAnalyzer contract and deterministic mock implementation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import UUID


class ImpactDirection(StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    MIXED = "mixed"
    UNCERTAIN = "uncertain"


class ImpactHorizon(StrEnum):
    IMMEDIATE = "immediate"
    SHORT_TERM = "short_term"
    MEDIUM_TERM = "medium_term"
    LONG_TERM = "long_term"


@dataclass(frozen=True, slots=True)
class ImpactRequest:
    event_candidate_id: UUID
    fact_summary: str | None
    evidence_count: int
    source_count: int
    official_evidence_present: bool
    affected_entity_refs: tuple[str, ...]
    affected_asset_refs: tuple[str, ...]
    affected_sector_refs: tuple[str, ...]
    uncertainty: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ImpactAnalysis:
    affected_companies: tuple[str, ...]
    affected_assets: tuple[str, ...]
    affected_sectors: tuple[str, ...]
    impact_direction: ImpactDirection
    impact_horizon: ImpactHorizon
    impact_channels: tuple[str, ...]
    confidence: float
    rationale_summary: str
    uncertainty: tuple[str, ...]
    required_market_validation: bool
    analysis_version: int


class ImpactAnalyzer(Protocol):
    async def analyze(self, request: ImpactRequest) -> ImpactAnalysis: ...


class DeterministicMockImpactAnalyzer:
    async def analyze(self, request: ImpactRequest) -> ImpactAnalysis:
        confidence = 0.5 if request.source_count > 1 else 0.25
        return ImpactAnalysis(
            affected_companies=request.affected_entity_refs,
            affected_assets=request.affected_asset_refs,
            affected_sectors=request.affected_sector_refs,
            impact_direction=ImpactDirection.UNCERTAIN,
            impact_horizon=ImpactHorizon.SHORT_TERM,
            impact_channels=("information",),
            confidence=confidence,
            rationale_summary="Deterministic mock; human-reviewed impact analysis is required.",
            uncertainty=request.uncertainty,
            required_market_validation=True,
            analysis_version=1,
        )


def validate_impact_analysis(value: ImpactAnalysis) -> tuple[str, ...]:
    errors: list[str] = []
    if not 0 <= value.confidence <= 1:
        errors.append("confidence_out_of_range")
    if value.analysis_version < 1:
        errors.append("analysis_version_invalid")
    if not value.rationale_summary.strip():
        errors.append("rationale_summary_required")
    forbidden = ("buy", "sell", "hold", "target price", "position sizing", "rebalance")
    if any(marker in value.rationale_summary.casefold() for marker in forbidden):
        errors.append("trading_action_language_forbidden")
    return tuple(errors)
