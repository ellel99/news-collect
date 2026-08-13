"""Provider-neutral, mockable ImpactAnalyzer benchmark foundation."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from market_intelligence.event_intelligence.analysis import (
    ImpactAnalysis,
    ImpactAnalyzer,
    ImpactRequest,
    validate_impact_analysis,
)
from market_intelligence.event_intelligence.fact_layer import FactSnapshot

BENCHMARK_MODELS = (
    "openai:gpt-5.6-terra",
    "anthropic:claude-sonnet-5",
    "google:gemini-pro",
    "deepseek:deepseek-v4-pro",
)


@dataclass(frozen=True, slots=True)
class ImpactBenchmarkCase:
    event_candidate_id: UUID
    fact_snapshot_hash: str
    category: str
    source_count: int
    evidence_count: int
    fact: FactSnapshot


@dataclass(frozen=True, slots=True)
class ImpactBenchmarkResult:
    model_key: str
    fact_snapshot_hash: str
    schema_valid: bool
    contract_valid: bool
    forbidden_language_detected: bool
    latency_ms: float
    input_tokens: int | None
    output_tokens: int | None
    estimated_cost: float | None
    impact_direction: str | None
    impact_horizon: str | None
    confidence: float | None
    affected_company_count: int
    affected_asset_count: int
    affected_sector_count: int
    uncertainty_count: int
    factual_fidelity_score: float | None = None
    causal_reasoning_score: float | None = None
    asset_mapping_score: float | None = None
    second_order_reasoning_score: float | None = None
    uncertainty_handling_score: float | None = None
    overall_quality_score: float | None = None
    safe_errors: tuple[str, ...] = ()


class BenchmarkAdapterTransport(Protocol):
    async def analyze(self, model_key: str, request: ImpactRequest) -> ImpactAnalysis: ...


@dataclass(frozen=True, slots=True)
class UnifiedBenchmarkAdapter:
    model_key: str
    transport: BenchmarkAdapterTransport

    async def analyze(self, request: ImpactRequest) -> ImpactAnalysis:
        if self.model_key not in BENCHMARK_MODELS:
            raise ValueError("benchmark_model_not_allowlisted")
        return await self.transport.analyze(self.model_key, request)


class OpenAIBenchmarkAdapter(UnifiedBenchmarkAdapter):
    pass


class AnthropicBenchmarkAdapter(UnifiedBenchmarkAdapter):
    pass


class GeminiBenchmarkAdapter(UnifiedBenchmarkAdapter):
    pass


class DeepSeekBenchmarkAdapter(UnifiedBenchmarkAdapter):
    pass


class ImpactBenchmarkRunner:
    async def run_one(
        self, case: ImpactBenchmarkCase, model_key: str, analyzer: ImpactAnalyzer
    ) -> ImpactBenchmarkResult:
        request = _request(case.fact)
        started = time.perf_counter()
        try:
            analysis = await analyzer.analyze(request)
            errors = validate_impact_analysis(analysis)
        except Exception:
            return _failed(model_key, case.fact_snapshot_hash, started, "benchmark_adapter_failed")
        forbidden = "trading_action_language_forbidden" in errors
        return ImpactBenchmarkResult(
            model_key=model_key,
            fact_snapshot_hash=case.fact_snapshot_hash,
            schema_valid=True,
            contract_valid=not errors,
            forbidden_language_detected=forbidden,
            latency_ms=(time.perf_counter() - started) * 1000,
            input_tokens=None,
            output_tokens=None,
            estimated_cost=None,
            impact_direction=analysis.impact_direction.value,
            impact_horizon=analysis.impact_horizon.value,
            confidence=analysis.confidence,
            affected_company_count=len(analysis.affected_companies),
            affected_asset_count=len(analysis.affected_assets),
            affected_sector_count=len(analysis.affected_sectors),
            uncertainty_count=len(analysis.uncertainty),
            safe_errors=errors,
        )


def _request(fact: FactSnapshot) -> ImpactRequest:
    return ImpactRequest(
        event_candidate_id=fact.event_candidate_id,
        fact_summary=fact.what_happened,
        evidence_count=fact.evidence_count,
        source_count=fact.source_count,
        official_evidence_present=fact.official_evidence_present,
        affected_entity_refs=fact.primary_entities,
        affected_asset_refs=fact.assets,
        affected_sector_refs=fact.sectors,
        uncertainty=fact.uncertainty + fact.contradictions,
        evidence_digests=fact.evidence_digests,
        analysis_input_quality=fact.analysis_input_quality.value,
    )


def _failed(
    model_key: str, snapshot_hash: str, started: float, error: str
) -> ImpactBenchmarkResult:
    return ImpactBenchmarkResult(
        model_key=model_key,
        fact_snapshot_hash=snapshot_hash,
        schema_valid=False,
        contract_valid=False,
        forbidden_language_detected=False,
        latency_ms=(time.perf_counter() - started) * 1000,
        input_tokens=None,
        output_tokens=None,
        estimated_cost=None,
        impact_direction=None,
        impact_horizon=None,
        confidence=None,
        affected_company_count=0,
        affected_asset_count=0,
        affected_sector_count=0,
        uncertainty_count=0,
        safe_errors=(error,),
    )
