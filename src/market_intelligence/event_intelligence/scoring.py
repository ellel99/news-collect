"""Deterministic importance scoring; this is not an investment score."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ImportanceInput:
    official_source: bool
    evidence_count: int
    source_count: int
    source_priority: int | None
    entity_count: int
    corroborated: bool


@dataclass(frozen=True, slots=True)
class ImportanceResult:
    score: float
    component_reasons: tuple[str, ...]
    scoring_version: int = 1


class ImportanceScorer(Protocol):
    def score(self, value: ImportanceInput) -> ImportanceResult: ...


class DeterministicImportanceScorer:
    def score(self, value: ImportanceInput) -> ImportanceResult:
        if value.evidence_count == 0:
            return ImportanceResult(score=0.0, component_reasons=("no_active_evidence:0",))
        score = 10.0
        reasons = ["base:10"]
        if value.official_source:
            score += 30
            reasons.append("official_source:30")
        diversity = min(20, max(0, value.source_count - 1) * 10)
        score += diversity
        reasons.append(f"source_diversity:{diversity}")
        corroboration = 15 if value.corroborated else 0
        score += corroboration
        reasons.append(f"corroboration:{corroboration}")
        entity = min(15, value.entity_count * 5)
        score += entity
        reasons.append(f"entity_relevance:{entity}")
        priority = min(10, max(0, value.source_priority or 0))
        score += priority
        reasons.append(f"source_priority:{priority}")
        return ImportanceResult(score=min(100.0, score), component_reasons=tuple(reasons))
