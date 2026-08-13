"""Explicitly bounded OpenAI Responses implementation; not a Foundation provider lock-in."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

import httpx

from market_intelligence.event_intelligence.analysis import (
    ImpactAnalysis,
    ImpactDirection,
    ImpactHorizon,
    ImpactRequest,
)
from market_intelligence.event_intelligence.fact_layer import evidence_digest_payload
from market_intelligence.providers.credentials import RuntimeCredential

_ENDPOINT = "https://api.openai.com/v1/responses"


class AnalyzerRuntimeError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


class StructuredAnalyzerTransport(Protocol):
    async def analyze(
        self,
        credential: RuntimeCredential,
        model: str,
        safe_input: dict[str, object],
    ) -> dict[str, object]: ...


@dataclass(frozen=True, slots=True)
class OpenAIResponsesImpactAnalyzer:
    credential: RuntimeCredential
    model: str
    transport: StructuredAnalyzerTransport

    async def analyze(self, request: ImpactRequest) -> ImpactAnalysis:
        payload = await self.transport.analyze(
            self.credential,
            self.model,
            {
                "event_candidate_id": str(request.event_candidate_id),
                "fact_summary": request.fact_summary,
                "evidence_count": request.evidence_count,
                "source_count": request.source_count,
                "official_evidence_present": request.official_evidence_present,
                "affected_entity_refs": list(request.affected_entity_refs),
                "affected_asset_refs": list(request.affected_asset_refs),
                "affected_sector_refs": list(request.affected_sector_refs),
                "uncertainty": list(request.uncertainty),
                "evidence_digests": [
                    evidence_digest_payload(item) for item in request.evidence_digests
                ],
                "analysis_input_quality": request.analysis_input_quality,
            },
        )
        return parse_impact_analysis(payload)


class OpenAIResponsesTransport:
    """One request, strict JSON schema, no response persistence or logging."""

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client

    async def analyze(
        self,
        credential: RuntimeCredential,
        model: str,
        safe_input: dict[str, object],
    ) -> dict[str, object]:
        body = {
            "model": model,
            "instructions": (
                "Analyze only the supplied event facts. Do not provide trading actions, "
                "target prices, position sizes, or recommendations."
            ),
            "input": json.dumps(safe_input, sort_keys=True),
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "impact_analysis",
                    "strict": True,
                    "schema": _schema(),
                }
            },
        }
        headers = {
            "Authorization": f"Bearer {credential.reveal_for_transport()}",
            "Content-Type": "application/json",
        }
        try:
            if self._client is not None:
                response = await self._client.post(
                    _ENDPOINT, json=body, headers=headers, timeout=30.0
                )
            else:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        _ENDPOINT, json=body, headers=headers, timeout=30.0
                    )
        except httpx.TimeoutException:
            raise AnalyzerRuntimeError("analyzer_timeout", retryable=True) from None
        except httpx.HTTPError:
            raise AnalyzerRuntimeError("analyzer_transport_failed", retryable=True) from None
        if response.status_code == 429:
            raise AnalyzerRuntimeError("analyzer_rate_limited", retryable=True)
        if response.status_code >= 500:
            raise AnalyzerRuntimeError("analyzer_upstream_failed", retryable=True)
        if response.status_code < 200 or response.status_code >= 300:
            raise AnalyzerRuntimeError("analyzer_request_rejected", retryable=False)
        try:
            payload = response.json()
            output = payload["output"]
            text = next(
                content["text"]
                for item in output
                for content in item.get("content", [])
                if content.get("type") == "output_text"
            )
            parsed = json.loads(text)
        except (KeyError, TypeError, ValueError, StopIteration):
            raise AnalyzerRuntimeError(
                "analyzer_structured_output_invalid", retryable=False
            ) from None
        if not isinstance(parsed, dict):
            raise AnalyzerRuntimeError("analyzer_structured_output_invalid", retryable=False)
        return parsed


def parse_impact_analysis(value: dict[str, object]) -> ImpactAnalysis:
    required = {
        "affected_companies",
        "affected_assets",
        "affected_sectors",
        "impact_direction",
        "impact_horizon",
        "impact_channels",
        "confidence",
        "rationale_summary",
        "uncertainty",
        "required_market_validation",
        "analysis_version",
    }
    if set(value) != required:
        raise AnalyzerRuntimeError("analyzer_contract_invalid", retryable=False)
    try:
        analysis = ImpactAnalysis(
            affected_companies=_strings(value["affected_companies"]),
            affected_assets=_strings(value["affected_assets"]),
            affected_sectors=_strings(value["affected_sectors"]),
            impact_direction=ImpactDirection(str(value["impact_direction"])),
            impact_horizon=ImpactHorizon(str(value["impact_horizon"])),
            impact_channels=_strings(value["impact_channels"]),
            confidence=_number(value["confidence"]),
            rationale_summary=str(value["rationale_summary"]),
            uncertainty=_strings(value["uncertainty"]),
            required_market_validation=_boolean(value["required_market_validation"]),
            analysis_version=_integer(value["analysis_version"]),
        )
    except (TypeError, ValueError):
        raise AnalyzerRuntimeError("analyzer_contract_invalid", retryable=False) from None
    return analysis


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise TypeError
    return tuple(value)


def _number(value: object) -> float:
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise TypeError
    return float(value)


def _boolean(value: object) -> bool:
    if not isinstance(value, bool):
        raise TypeError
    return value


def _integer(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError
    return value


def _schema() -> dict[str, object]:
    array = {"type": "array", "items": {"type": "string"}}
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "affected_companies": array,
            "affected_assets": array,
            "affected_sectors": array,
            "impact_direction": {
                "type": "string",
                "enum": ["positive", "negative", "mixed", "uncertain"],
            },
            "impact_horizon": {
                "type": "string",
                "enum": ["immediate", "short_term", "medium_term", "long_term"],
            },
            "impact_channels": array,
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "rationale_summary": {"type": "string"},
            "uncertainty": array,
            "required_market_validation": {"type": "boolean"},
            "analysis_version": {"type": "integer", "minimum": 1},
        },
        "required": [
            "affected_companies",
            "affected_assets",
            "affected_sectors",
            "impact_direction",
            "impact_horizon",
            "impact_channels",
            "confidence",
            "rationale_summary",
            "uncertainty",
            "required_market_validation",
            "analysis_version",
        ],
    }
