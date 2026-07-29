from __future__ import annotations

from collections.abc import Mapping

import httpx

from market_intelligence.providers.preflight import base
from market_intelligence.providers.preflight.base import RequestSpec, SmokeReport, SmokeResult

PROVIDER = "finnhub"
ENDPOINT = "https://finnhub.io/api/v1/quote"


def build_request(
    environ: Mapping[str, str],
    *,
    symbol: str,
    execute: bool,
) -> RequestSpec:
    api_key = base.require_secret("FINNHUB_API_KEY", dict(environ), execute=execute)
    return RequestSpec(
        provider=PROVIDER,
        method="GET",
        url=ENDPOINT,
        params={"symbol": symbol},
        headers={"Accept": "application/json", "X-Finnhub-Token": api_key},
        json_body=None,
        secret_values=(api_key,),
        item_path=(),
        columnar_items=False,
        rate_limit_headers=("X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset"),
    )


def redact_request(request: RequestSpec) -> dict[str, object]:
    return base.redact_request(request)


def execute_minimal_request(
    request: RequestSpec, *, transport: httpx.BaseTransport | None = None
) -> SmokeReport:
    return base.execute_minimal_request(request, transport=transport)


def summarize_response_shape(payload: object) -> tuple[list[str], list[str], int | None]:
    return base.summarize_response_shape(payload, item_path=())


def classify_smoke_result(status: int | None, *, valid_json: bool) -> SmokeResult:
    return base.classify_smoke_result(status, valid_json=valid_json)
