from __future__ import annotations

from collections.abc import Mapping

import httpx

from market_intelligence.providers.preflight import base
from market_intelligence.providers.preflight.base import RequestSpec, SmokeReport, SmokeResult

PROVIDER = "sec_edgar"
ENDPOINT = "https://data.sec.gov/submissions/CIK0000320193.json"


def build_request(
    environ: Mapping[str, str],
    *,
    ticker: str,
    execute: bool,
) -> RequestSpec:
    if ticker.upper() != "AAPL":
        raise ValueError("the scaffold only permits the documented AAPL/CIK0000320193 smoke")
    user_agent = base.require_secret("SEC_USER_AGENT", dict(environ), execute=execute)
    contact = base.require_secret("SEC_CONTACT_EMAIL", dict(environ), execute=execute)
    return RequestSpec(
        provider=PROVIDER,
        method="GET",
        url=ENDPOINT,
        params={},
        headers={
            "Accept": "application/json",
            "User-Agent": f"{user_agent} {contact}",
        },
        json_body=None,
        secret_values=(user_agent, contact),
        item_path=("filings", "recent"),
        columnar_items=True,
        required_any_item_fields=frozenset(
            {"accessionNumber", "filingDate", "form", "primaryDocument"}
        ),
        rate_limit_headers=(),
    )


def redact_request(request: RequestSpec) -> dict[str, object]:
    return base.redact_request(request)


def execute_minimal_request(
    request: RequestSpec, *, transport: httpx.BaseTransport | None = None
) -> SmokeReport:
    report = base.execute_minimal_request(request, transport=transport)
    return report


def summarize_response_shape(payload: object) -> tuple[list[str], list[str], int | None]:
    return base.summarize_response_shape(
        payload,
        item_path=("filings", "recent"),
        columnar_items=True,
    )


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
        required_any_item_fields=frozenset(
            {"accessionNumber", "filingDate", "form", "primaryDocument"}
        ),
    )
