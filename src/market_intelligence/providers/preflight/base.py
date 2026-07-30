from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

import httpx

SmokeResult = Literal["PASS", "BLOCKED", "FAIL"]


class MissingCredentialError(ValueError):
    """Raised before network access when required local credentials are absent."""


@dataclass(frozen=True, slots=True)
class RequestSpec:
    provider: str
    method: Literal["GET", "POST"]
    url: str
    params: dict[str, str | int]
    headers: dict[str, str]
    json_body: dict[str, Any] | None
    secret_values: tuple[str, ...]
    item_path: tuple[str, ...]
    columnar_items: bool
    required_any_item_fields: frozenset[str]
    rate_limit_headers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SmokeReport:
    provider: str
    endpoint_family: str
    http_status: int | None
    valid_json: bool
    top_level_fields: list[str]
    item_fields: list[str]
    result_count: int | None
    rate_limit_headers_present: list[str]
    retry_after_present: bool
    classified_result: SmokeResult

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def require_secret(name: str, environ: dict[str, str], *, execute: bool) -> str:
    value = environ.get(name, "").strip()
    if execute and not value:
        raise MissingCredentialError(f"required environment variable is missing: {name}")
    return value or f"<env:{name}>"


def redact_request(request: RequestSpec) -> dict[str, object]:
    return {
        "provider": request.provider,
        "method": request.method,
        "endpoint_family": request.url,
        "param_names": sorted(request.params),
        "header_names": sorted(request.headers),
        "json_field_names": sorted(request.json_body or {}),
    }


def _value_at_path(payload: object, path: tuple[str, ...]) -> object:
    current = payload
    for part in path:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def summarize_response_shape(
    payload: object,
    *,
    item_path: tuple[str, ...],
    columnar_items: bool = False,
) -> tuple[list[str], list[str], int | None]:
    top_level_fields = sorted(payload) if isinstance(payload, dict) else []
    items = _value_at_path(payload, item_path)
    if isinstance(items, list):
        first = items[0] if items else None
        item_fields = sorted(first) if isinstance(first, dict) else []
        return top_level_fields, item_fields, len(items)
    if isinstance(items, dict):
        item_fields = sorted(items)
        if columnar_items:
            lengths = [len(value) for value in items.values() if isinstance(value, list)]
            return top_level_fields, item_fields, max(lengths, default=0)
        return top_level_fields, item_fields, 1 if items else 0
    return top_level_fields, [], None


def classify_smoke_result(
    status: int | None,
    *,
    valid_json: bool,
    result_count: int | None = None,
    item_fields: list[str] | None = None,
    required_any_item_fields: frozenset[str] = frozenset(),
) -> SmokeResult:
    if status is None or status in {401, 402, 403, 429} or (status >= 500):
        return "BLOCKED"
    if not (200 <= status < 300) or not valid_json:
        return "FAIL"
    if result_count is None or result_count <= 0 or not item_fields:
        return "FAIL"
    if required_any_item_fields and required_any_item_fields.isdisjoint(item_fields):
        return "FAIL"
    return "PASS"


def execute_minimal_request(
    request: RequestSpec,
    *,
    timeout_seconds: float = 20.0,
    transport: httpx.BaseTransport | None = None,
) -> SmokeReport:
    try:
        with httpx.Client(
            timeout=timeout_seconds,
            transport=transport,
            follow_redirects=False,
        ) as client:
            response = client.request(
                request.method,
                request.url,
                params=request.params,
                headers=request.headers,
                json=request.json_body,
            )
    except httpx.HTTPError:
        return SmokeReport(
            provider=request.provider,
            endpoint_family=request.url,
            http_status=None,
            valid_json=False,
            top_level_fields=[],
            item_fields=[],
            result_count=None,
            rate_limit_headers_present=[],
            retry_after_present=False,
            classified_result="BLOCKED",
        )

    try:
        payload: object = response.json()
        valid_json = True
    except ValueError:
        payload = None
        valid_json = False

    top_fields, item_fields, count = summarize_response_shape(
        payload,
        item_path=request.item_path,
        columnar_items=request.columnar_items,
    )
    header_names = {name.lower() for name in response.headers}
    rate_headers = sorted(
        header for header in request.rate_limit_headers if header.lower() in header_names
    )
    return SmokeReport(
        provider=request.provider,
        endpoint_family=request.url,
        http_status=response.status_code,
        valid_json=valid_json,
        top_level_fields=top_fields,
        item_fields=item_fields,
        result_count=count,
        rate_limit_headers_present=rate_headers,
        retry_after_present="retry-after" in header_names,
        classified_result=classify_smoke_result(
            response.status_code,
            valid_json=valid_json,
            result_count=count,
            item_fields=item_fields,
            required_any_item_fields=request.required_any_item_fields,
        ),
    )


def blocked_report(provider: str, endpoint_family: str) -> SmokeReport:
    return SmokeReport(
        provider=provider,
        endpoint_family=endpoint_family,
        http_status=None,
        valid_json=False,
        top_level_fields=[],
        item_fields=[],
        result_count=None,
        rate_limit_headers_present=[],
        retry_after_present=False,
        classified_result="BLOCKED",
    )
