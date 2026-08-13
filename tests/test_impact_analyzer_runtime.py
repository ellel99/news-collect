from __future__ import annotations

import importlib.util
import inspect
import sys
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from market_intelligence.event_intelligence.analysis import (
    ImpactDirection,
    ImpactHorizon,
    ImpactRequest,
    validate_impact_analysis,
)
from market_intelligence.event_intelligence.analyzers import (
    AnalyzerRuntimeError,
    OpenAIResponsesImpactAnalyzer,
    OpenAIResponsesTransport,
)
from market_intelligence.event_intelligence.analyzers.openai_responses import parse_impact_analysis
from market_intelligence.providers.credentials import RuntimeCredential

_SCRIPT_SPEC = importlib.util.spec_from_file_location(
    "event_impact_smoke",
    Path(__file__).parents[1] / "scripts" / "event_impact_smoke.py",
)
assert _SCRIPT_SPEC is not None and _SCRIPT_SPEC.loader is not None
event_impact_smoke = importlib.util.module_from_spec(_SCRIPT_SPEC)
sys.modules[_SCRIPT_SPEC.name] = event_impact_smoke
_SCRIPT_SPEC.loader.exec_module(event_impact_smoke)
dry_run = event_impact_smoke.dry_run
execute = event_impact_smoke.execute

SECRET = "synthetic-secret-never-log"


def payload(**changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "affected_companies": ["entity:acme"],
        "affected_assets": ["asset:acme"],
        "affected_sectors": ["sector:technology"],
        "impact_direction": "uncertain",
        "impact_horizon": "short_term",
        "impact_channels": ["information"],
        "confidence": 0.5,
        "rationale_summary": "Potential impact requires validation.",
        "uncertainty": ["single_source"],
        "required_market_validation": True,
        "analysis_version": 1,
    }
    value.update(changes)
    return value


@pytest.mark.parametrize("direction", [item.value for item in ImpactDirection])
@pytest.mark.parametrize("horizon", [item.value for item in ImpactHorizon])
def test_structured_contract_accepts_direction_and_horizon_matrix(
    direction: str, horizon: str
) -> None:
    result = parse_impact_analysis(payload(impact_direction=direction, impact_horizon=horizon))
    assert validate_impact_analysis(result) == ()


@pytest.mark.parametrize(
    "bad",
    [
        {"confidence": 2},
        {"impact_direction": "up"},
        {"impact_horizon": "forever"},
        {"rationale_summary": "BUY now"},
    ],
)
def test_invalid_structured_output_fails_closed(bad: dict[str, object]) -> None:
    if "rationale_summary" in bad:
        result = parse_impact_analysis(payload(**bad))
        assert "trading_action_language_forbidden" in validate_impact_analysis(result)
    else:
        with pytest.raises((AnalyzerRuntimeError, ValueError)):
            result = parse_impact_analysis(payload(**bad))
            if validate_impact_analysis(result):
                raise ValueError


def test_missing_or_extra_field_fails_closed() -> None:
    value = payload()
    value.pop("confidence")
    with pytest.raises(AnalyzerRuntimeError):
        parse_impact_analysis(value)


@pytest.mark.asyncio
async def test_mocked_openai_response_success_is_structural_and_secret_safe() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == f"Bearer {SECRET}"
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "content": [
                            {"type": "output_text", "text": __import__("json").dumps(payload())}
                        ]
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        analyzer = OpenAIResponsesImpactAnalyzer(
            RuntimeCredential("OPENAI_API_KEY", SECRET),
            "synthetic-model",
            OpenAIResponsesTransport(client),
        )
        result = await analyzer.analyze(_request())
    assert result.impact_direction is ImpactDirection.UNCERTAIN
    assert SECRET not in repr(analyzer)


@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [
        (429, "analyzer_rate_limited", True),
        (500, "analyzer_upstream_failed", True),
        (400, "analyzer_request_rejected", False),
    ],
)
@pytest.mark.asyncio
async def test_http_failures_are_safely_classified(status: int, code: str, retryable: bool) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(status, json={}))
    ) as client:
        analyzer = OpenAIResponsesImpactAnalyzer(
            RuntimeCredential("OPENAI_API_KEY", SECRET),
            "synthetic",
            OpenAIResponsesTransport(client),
        )
        with pytest.raises(AnalyzerRuntimeError) as caught:
            await analyzer.analyze(_request())
    assert (caught.value.code, caught.value.retryable) == (code, retryable)


@pytest.mark.asyncio
async def test_timeout_is_retryable() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("synthetic", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
        transport = OpenAIResponsesTransport(client)
        with pytest.raises(AnalyzerRuntimeError, match="analyzer_timeout") as caught:
            await transport.analyze(RuntimeCredential("OPENAI_API_KEY", SECRET), "model", {})
    assert caught.value.retryable


@pytest.mark.asyncio
async def test_smoke_dry_run_and_missing_config_are_inert(monkeypatch: pytest.MonkeyPatch) -> None:
    summary = dry_run()
    assert summary.status == "DRY_RUN"
    assert not summary.credential_read and not summary.request_enabled
    blocked = await execute({})
    assert blocked.status == "BLOCKED"
    assert not blocked.request_enabled


def test_event_runtime_source_has_no_phase1_or_trading_dependencies() -> None:
    root = Path(__file__).parents[1]
    sources = "\n".join(
        (root / path).read_text()
        for path in (
            "src/market_intelligence/event_intelligence/fact_layer.py",
            "src/market_intelligence/event_intelligence/runtime.py",
            "src/market_intelligence/event_intelligence/persistence.py",
        )
    ).casefold()
    for marker in (
        "provider_capture",
        "local_evaluation",
        "telegram",
        "recommendation",
        "portfolio",
        "holding",
    ):
        assert marker not in sources
    assert not inspect.iscoroutinefunction(dry_run)


def _request() -> ImpactRequest:
    return ImpactRequest(uuid4(), "Synthetic fact", 1, 1, False, (), (), (), ("single_source",))
