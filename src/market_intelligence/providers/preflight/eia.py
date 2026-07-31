from __future__ import annotations

from collections.abc import Mapping

import httpx

from market_intelligence.providers.preflight import base
from market_intelligence.providers.preflight.base import RequestSpec, SmokeReport, SmokeResult

PROVIDER = "eia"
ENDPOINT = "https://api.eia.gov/v2/electricity/retail-sales/data/"


def build_request(
    environ: Mapping[str, str],
    *,
    dataset: str,
    limit: int,
    execute: bool,
) -> RequestSpec:
    api_key = base.require_secret("EIA_API_KEY", dict(environ), execute=execute)
    version = environ.get("EIA_API_VERSION", "v2")
    if version != "v2":
        raise ValueError("EIA_API_VERSION must be v2")
    if dataset != "electricity":
        raise ValueError("the scaffold only permits the electricity smoke dataset")
    return RequestSpec(
        provider=PROVIDER,
        method="GET",
        url=ENDPOINT,
        params={
            "api_key": api_key,
            "data[]": "price",
            "frequency": "monthly",
            "length": limit,
            "sort[0][column]": "period",
            "sort[0][direction]": "desc",
        },
        headers={"Accept": "application/json"},
        json_body=None,
        secret_values=(api_key,),
        item_path=("response", "data"),
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
    return base.summarize_response_shape(payload, item_path=("response", "data"))


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
