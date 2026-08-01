#!/usr/bin/env python3
"""Audit local provider captures without network access or content output."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CAPTURE_ROOT = REPOSITORY_ROOT / "local_evaluation" / "raw_provider_captures"
FORBIDDEN_SECRET_KEYS = {
    "api_key",
    "api_token",
    "authorization",
    "token",
    "x-finnhub-token",
}
SECRET_QUERY_MARKERS = ("api_key=", "api_token=", "token=")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit local raw provider captures")
    parser.add_argument("--capture-dir", type=Path, default=CAPTURE_ROOT)
    return parser


def _walk(value: object) -> Iterator[tuple[str, object]]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key), child
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _has_secret(capture: object) -> bool:
    return any(
        key.lower() in FORBIDDEN_SECRET_KEYS and value not in (None, "", [], {})
        for key, value in _walk(capture)
    )


def _has_secret_url(capture: object) -> bool:
    for key, value in _walk(capture):
        if key.lower() in {"request_url", "url"} and isinstance(value, str):
            lowered = value.lower()
            if any(marker in lowered for marker in SECRET_QUERY_MARKERS):
                return True
    endpoint = capture.get("endpoint_family") if isinstance(capture, dict) else None
    return isinstance(endpoint, str) and any(
        marker in endpoint.lower() for marker in SECRET_QUERY_MARKERS
    )


def _has_authorization_header(capture: object) -> bool:
    return any(
        key.lower() in {"authorization", "x-finnhub-token"} and value not in (None, "", [], {})
        for key, value in _walk(capture)
    )


def _body(capture: dict[str, Any]) -> object:
    return capture.get("response_body")


def _shape(capture: dict[str, Any]) -> tuple[list[str], list[str], int | None]:
    provider = capture.get("provider")
    body = _body(capture)
    top_fields = sorted(body) if isinstance(body, dict) else []
    if not isinstance(body, dict):
        return top_fields, [], None
    if provider == "marketaux":
        items = body.get("data")
        if isinstance(items, list):
            first = items[0] if items else None
            return top_fields, sorted(first) if isinstance(first, dict) else [], len(items)
    if provider == "finnhub":
        return top_fields, top_fields, 1 if body else 0
    if provider == "eia":
        response = body.get("response")
        items = response.get("data") if isinstance(response, dict) else None
        if isinstance(items, list):
            first = items[0] if items else None
            return top_fields, sorted(first) if isinstance(first, dict) else [], len(items)
    if provider == "sec_edgar":
        filings = body.get("filings")
        recent = filings.get("recent") if isinstance(filings, dict) else None
        if isinstance(recent, dict):
            lengths = [len(value) for value in recent.values() if isinstance(value, list)]
            return top_fields, sorted(recent), max(lengths, default=0)
    return top_fields, [], None


def _request_data(capture: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    request = capture.get("request")
    if not isinstance(request, dict):
        return {}, {}
    context = request.get("context")
    return request, context if isinstance(context, dict) else {}


def _within_limit(provider: object, limit: object, count: int | None) -> bool:
    if not isinstance(limit, int) or count is None:
        return False
    if provider == "marketaux":
        return 1 <= limit <= 3 and count <= limit and count <= 15
    if provider == "finnhub":
        return limit == 1 and count <= 1
    if provider == "eia":
        return 1 <= limit <= 5 and count <= limit
    if provider == "sec_edgar":
        return 1 <= limit <= 10 and count <= limit
    return False


def audit_capture(path: Path) -> dict[str, object]:
    errors: list[str] = []
    raw = path.read_bytes()
    try:
        loaded: object = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        loaded = {}
        errors.append("invalid_json")
    capture = loaded if isinstance(loaded, dict) else {}
    if not isinstance(loaded, dict):
        errors.append("capture_must_be_object")
    top_fields, item_fields, count = _shape(capture)
    request, context = _request_data(capture)
    secret_detected = _has_secret(capture)
    secret_url = _has_secret_url(capture)
    authorization_header = _has_authorization_header(capture)
    limit = request.get("limit")
    within_limit = _within_limit(capture.get("provider"), limit, count)
    if secret_detected:
        errors.append("secret_field_detected")
    if secret_url:
        errors.append("raw_request_url_with_secret")
    if authorization_header:
        errors.append("authorization_header_detected")
    if not within_limit:
        errors.append("capture_outside_limit")
    headers = capture.get("safe_response_headers")
    header_names = sorted(headers) if isinstance(headers, dict) else []
    return {
        "provider": capture.get("provider"),
        "capture_file": str(path),
        "file_size_bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "captured_at": capture.get("captured_at"),
        "http_status": capture.get("http_status"),
        "top_level_fields": top_fields,
        "item_fields": item_fields,
        "result_count": count,
        "safe_header_names": header_names,
        "query": context.get("query"),
        "symbol": context.get("symbol"),
        "dataset": context.get("dataset"),
        "ticker": context.get("ticker"),
        "limit": limit,
        "has_secret_detected": secret_detected,
        "has_raw_request_url_with_secret": secret_url,
        "has_authorization_header": authorization_header,
        "within_limit": within_limit,
        "replay_ready": (
            not errors
            and isinstance(capture.get("http_status"), int)
            and 200 <= capture["http_status"] < 300
        ),
        "errors": errors,
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    capture_dir = args.capture_dir.resolve()
    reports = [audit_capture(path) for path in sorted(capture_dir.glob("*.json"))]
    print(json.dumps({"reports": reports}, sort_keys=True))
    return 0 if all(report["replay_ready"] for report in reports) else 2


if __name__ == "__main__":
    raise SystemExit(main())
