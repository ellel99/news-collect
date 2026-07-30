from __future__ import annotations

from collections.abc import Mapping

import httpx

from market_intelligence.providers.preflight import base
from market_intelligence.providers.preflight.base import RequestSpec, SmokeReport, SmokeResult

PROVIDER = "marketaux"
ENDPOINT = "https://api.marketaux.com/v1/news/all"


def build_request(
    environ: Mapping[str, str],
    *,
    query: str,
    limit: int,
    execute: bool,
) -> RequestSpec:
    token = base.require_secret("MARKETAUX_API_TOKEN", dict(environ), execute=execute)
    return RequestSpec(
        provider=PROVIDER,
        method="GET",
        url=ENDPOINT,
        params={"api_token": token, "limit": limit, "page": 1, "search": query},
        headers={"Accept": "application/json"},
        json_body=None,
        secret_values=(token,),
        item_path=("data",),
        columnar_items=False,
        required_any_item_fields=frozenset(),
        rate_limit_headers=(
            "X-RateLimit-Limit",
            "X-RateLimit-Remaining",
            "X-UsageLimit-Limit",
            "X-UsageLimit-Remaining",
        ),
    )


def redact_request(request: RequestSpec) -> dict[str, object]:
    return base.redact_request(request)


def execute_minimal_request(
    request: RequestSpec, *, transport: httpx.BaseTransport | None = None
) -> SmokeReport:
    return base.execute_minimal_request(request, transport=transport)


def summarize_response_shape(payload: object) -> tuple[list[str], list[str], int | None]:
    return base.summarize_response_shape(payload, item_path=("data",))


def classify_smoke_result(
    status: int | None,
    *,
    valid_json: bool,
    result_count: int | None = None,
    item_fields: list[str] | None = None,
) -> SmokeResult:
    return base.classify_smoke_result(
        status,
        valid_json=valid_json,
        result_count=result_count,
        item_fields=item_fields,
    )
