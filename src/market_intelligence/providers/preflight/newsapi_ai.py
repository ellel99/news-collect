from __future__ import annotations

from collections.abc import Mapping

import httpx

from market_intelligence.providers.preflight import base
from market_intelligence.providers.preflight.base import (
    RequestSpec,
    SmokeReport,
    SmokeResult,
)

PROVIDER = "newsapi_ai"
ENDPOINT = "https://eventregistry.org/api/v1/article/getArticles"


def build_request(
    environ: Mapping[str, str],
    *,
    query: str,
    max_results: int,
    execute: bool,
) -> RequestSpec:
    api_key = base.require_secret("NEWSAPI_AI_API_KEY", dict(environ), execute=execute)
    return RequestSpec(
        provider=PROVIDER,
        method="POST",
        url=ENDPOINT,
        params={},
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        json_body={
            "action": "getArticles",
            "apiKey": api_key,
            "articlesCount": max_results,
            "articlesPage": 1,
            "articlesSortBy": "date",
            "dataType": ["news"],
            "forceMaxDataTimeWindow": 7,
            "keyword": query,
            "resultType": "articles",
        },
        secret_values=(api_key,),
        item_path=("articles", "results"),
        columnar_items=False,
        required_any_item_fields=frozenset(),
        rate_limit_headers=(),
    )


def redact_request(request: RequestSpec) -> dict[str, object]:
    return base.redact_request(request)


def execute_minimal_request(
    request: RequestSpec, *, transport: httpx.BaseTransport | None = None
) -> SmokeReport:
    return base.execute_minimal_request(request, transport=transport)


def summarize_response_shape(payload: object) -> tuple[list[str], list[str], int | None]:
    return base.summarize_response_shape(payload, item_path=("articles", "results"))


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
