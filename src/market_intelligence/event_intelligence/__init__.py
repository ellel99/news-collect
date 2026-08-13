"""Provider-neutral Event Candidate foundation."""

from market_intelligence.event_intelligence.analysis import (
    DeterministicMockImpactAnalyzer,
    ImpactAnalysis,
    ImpactAnalyzer,
    ImpactDirection,
    ImpactHorizon,
    validate_impact_analysis,
)
from market_intelligence.event_intelligence.fact_layer import FactLayerBuilder, FactSnapshot
from market_intelligence.event_intelligence.matching import (
    EvidenceProjection,
    MatchDecision,
    MatchRule,
)
from market_intelligence.event_intelligence.persistence import (
    AnalyzerIdentity,
    ImpactAnalysisStore,
)
from market_intelligence.event_intelligence.runtime import (
    EventProcessingRuntime,
    EventRuntimeStatus,
)
from market_intelligence.event_intelligence.scoring import (
    DeterministicImportanceScorer,
    ImportanceResult,
    ImportanceScorer,
)
from market_intelligence.event_intelligence.service import (
    EventCandidateOutcome,
    EventCandidateService,
)

__all__ = [
    "AnalyzerIdentity",
    "DeterministicImportanceScorer",
    "DeterministicMockImpactAnalyzer",
    "EventCandidateOutcome",
    "EventCandidateService",
    "EventProcessingRuntime",
    "EventRuntimeStatus",
    "EvidenceProjection",
    "FactLayerBuilder",
    "FactSnapshot",
    "ImpactAnalysis",
    "ImpactAnalysisStore",
    "ImpactAnalyzer",
    "ImpactDirection",
    "ImpactHorizon",
    "ImportanceResult",
    "ImportanceScorer",
    "MatchDecision",
    "MatchRule",
    "validate_impact_analysis",
]
