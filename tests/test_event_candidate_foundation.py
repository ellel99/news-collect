from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from market_intelligence.event_intelligence.analysis import (
    DeterministicMockImpactAnalyzer,
    ImpactAnalysis,
    ImpactDirection,
    ImpactHorizon,
    ImpactRequest,
    validate_impact_analysis,
)
from market_intelligence.event_intelligence.matching import (
    CandidateProjection,
    EvidenceProjection,
    MatchRule,
    canonicalize_url,
    evidence_signatures,
    match_existing,
)
from market_intelligence.event_intelligence.scoring import (
    DeterministicImportanceScorer,
    ImportanceInput,
)

NOW = datetime(2026, 8, 13, tzinfo=UTC)


def evidence(**changes: object) -> EvidenceProjection:
    values: dict[str, object] = {
        "evidence_item_id": uuid4(),
        "provider": "marketaux",
        "provider_item_id": "item-1",
        "provider_item_hash": "a" * 64,
        "official_source": False,
        "canonical_url": None,
        "title": "Acme files quarterly report",
        "event_time": NOW,
        "observed_at": NOW,
        "entity_refs": ("entity:acme",),
        "asset_refs": ("asset:acme",),
        "topic_refs": (),
    }
    values.update(changes)
    return EvidenceProjection(**values)  # type: ignore[arg-type]


def candidate(**changes: object) -> CandidateProjection:
    values: dict[str, object] = {
        "id": uuid4(),
        "cluster_key": "b" * 64,
        "strong_identity_hash": None,
        "identity_signatures": (),
        "title_fingerprints": (),
        "first_seen_at": NOW,
        "latest_seen_at": NOW,
        "entities": ("entity:acme",),
    }
    values.update(changes)
    return CandidateProjection(**values)  # type: ignore[arg-type]


def test_ambiguous_match_fails_closed_to_new_candidate() -> None:
    incoming = evidence(provider_item_id=None)
    fingerprint = match_existing(incoming, ()).title_fingerprint
    candidates = (
        candidate(title_fingerprints=(fingerprint,)),
        candidate(title_fingerprints=(fingerprint,)),
    )
    result = match_existing(incoming, candidates)
    assert result.candidate_id is None
    assert result.match_rule is MatchRule.AMBIGUOUS_NEW_CANDIDATE


def test_same_company_alone_does_not_merge() -> None:
    result = match_existing(
        evidence(title="Different fact", provider_item_id=None),
        (candidate(title_fingerprints=("unrelated",)),),
    )
    assert result.candidate_id is None
    assert result.match_rule is MatchRule.NEW_CANDIDATE


def test_same_canonical_url_groups_across_providers() -> None:
    original = evidence(canonical_url="https://EXAMPLE.test:443/news/?utm_source=one")
    existing = candidate(identity_signatures=evidence_signatures(original))
    incoming = evidence(
        provider="sec_edgar",
        provider_item_id="filing-1",
        official_source=True,
        canonical_url="https://example.test/news",
    )
    result = match_existing(incoming, (existing,))
    assert result.candidate_id == existing.id
    assert result.match_rule is MatchRule.CANONICAL_URL


def test_business_query_parameters_are_preserved_and_do_not_false_merge() -> None:
    first = evidence(canonical_url="https://example.test/article?id=100")
    existing = candidate(identity_signatures=evidence_signatures(first))
    incoming = evidence(
        provider="sec_edgar",
        provider_item_id="filing-2",
        official_source=True,
        canonical_url="https://example.test/article?id=200",
    )
    result = match_existing(incoming, (existing,))
    assert result.candidate_id is None


def test_tracking_only_query_is_removed_with_stable_query_normalization() -> None:
    assert canonicalize_url(
        "HTTPS://Example.TEST:443/article/?z=2&utm_source=x&a=1#fragment"
    ) == canonicalize_url("https://example.test/article?a=1&z=2")


def test_expired_time_window_does_not_merge() -> None:
    incoming = evidence(provider_item_id=None, event_time=NOW + timedelta(days=3))
    fingerprint = match_existing(incoming, ()).title_fingerprint
    result = match_existing(
        incoming,
        (candidate(title_fingerprints=(fingerprint,)),),
    )
    assert result.candidate_id is None


def test_importance_scorer_is_deterministic_bounded_and_explainable() -> None:
    scorer = DeterministicImportanceScorer()
    value = ImportanceInput(True, 3, 2, 8, 2, True)
    first = scorer.score(value)
    assert first == scorer.score(value)
    assert 0 <= first.score <= 100
    assert "official_source:30" in first.component_reasons
    rendered = " ".join(first.component_reasons).casefold()
    assert all(word not in rendered for word in ("buy", "sell", "hold", "rebalance"))


@pytest.mark.asyncio
async def test_mock_impact_analyzer_contract_is_safe_and_io_free() -> None:
    request = ImpactRequest(
        event_candidate_id=uuid4(),
        fact_summary=None,
        evidence_count=2,
        source_count=2,
        official_evidence_present=True,
        affected_entity_refs=("entity:acme",),
        affected_asset_refs=("asset:acme",),
        affected_sector_refs=("sector:technology",),
    )
    result = await DeterministicMockImpactAnalyzer().analyze(request)
    assert result.impact_direction is ImpactDirection.UNCERTAIN
    assert result.impact_horizon is ImpactHorizon.SHORT_TERM
    assert validate_impact_analysis(result) == ()


@pytest.mark.parametrize("direction", list(ImpactDirection))
@pytest.mark.parametrize("horizon", list(ImpactHorizon))
def test_impact_contract_accepts_approved_enum_matrix(
    direction: ImpactDirection, horizon: ImpactHorizon
) -> None:
    value = ImpactAnalysis(
        (),
        (),
        (),
        direction,
        horizon,
        ("information",),
        0.5,
        "Evidence impact remains uncertain.",
        (),
        True,
        1,
    )
    assert validate_impact_analysis(value) == ()


def test_trading_action_language_is_rejected() -> None:
    value = ImpactAnalysis(
        (),
        (),
        (),
        ImpactDirection.POSITIVE,
        ImpactHorizon.IMMEDIATE,
        (),
        0.5,
        "BUY and HOLD",
        (),
        False,
        1,
    )
    assert "trading_action_language_forbidden" in validate_impact_analysis(value)


def test_event_foundation_has_no_runtime_ai_or_phase1_scheduler_dependency() -> None:
    from pathlib import Path

    source = "\n".join(
        path.read_text() for path in Path("src/market_intelligence/event_intelligence").glob("*.py")
    ).casefold()
    for forbidden in (
        "requests",
        "telegram",
        "scheduler",
        "provider_capture",
        "local_evaluation",
        "portfolio",
    ):
        assert forbidden not in source
