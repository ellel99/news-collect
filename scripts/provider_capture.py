#!/usr/bin/env python3
"""Create one bounded, local-only provider response capture.

Dry-run is the default. Captures are intentionally incompatible with the
collection runner and may only be written below ``local_evaluation``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

import httpx

from market_intelligence.providers.preflight import base, eia, finnhub, marketaux
from market_intelligence.providers.preflight.base import MissingCredentialError, RequestSpec
from market_intelligence.providers.preflight.local_env import load_environment

CAPTURE_VERSION = 1
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CAPTURE_ROOT = REPOSITORY_ROOT / "local_evaluation" / "raw_provider_captures"
SAFE_RESPONSE_HEADERS = frozenset(
    {
        "content-type",
        "retry-after",
        "x-ratelimit-limit",
        "x-ratelimit-remaining",
        "x-ratelimit-reset",
        "x-usagelimit-limit",
        "x-usagelimit-remaining",
    }
)
SECRET_PARAM_NAMES = frozenset({"api_key", "api_token", "token"})
SECRET_HEADER_NAMES = frozenset({"authorization", "user-agent", "x-finnhub-token"})
FORBIDDEN_RESPONSE_KEYS = SECRET_PARAM_NAMES | frozenset({"authorization", "x-finnhub-token"})
SECRET_QUERY_MARKERS = ("api_key=", "api_token=", "token=")
SEC_CIK_BY_TICKER = {
    "AAPL": "0000320193",
    "MSFT": "0000789019",
    "NVDA": "0001045810",
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create one local provider capture")
    parser.add_argument(
        "--provider",
        required=True,
        choices=("marketaux", "finnhub", "eia", "sec_edgar"),
    )
    parser.add_argument("--query", default="artificial intelligence")
    parser.add_argument("--symbol", default="AAPL")
    parser.add_argument("--dataset", default="electricity")
    parser.add_argument("--ticker", default="AAPL")
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--execute", action="store_true")
    return parser


def _validate_args(args: argparse.Namespace) -> None:
    if args.provider == "marketaux" and not 1 <= args.limit <= 3:
        raise ValueError("Marketaux limit must be between 1 and 3")
    if args.provider == "eia" and (args.dataset != "electricity" or not 1 <= args.limit <= 5):
        raise ValueError("EIA requires dataset=electricity and limit between 1 and 5")
    if args.provider == "finnhub" and (not args.symbol.strip() or args.limit != 1):
        raise ValueError("Finnhub requires one symbol and limit=1")
    if args.provider == "sec_edgar":
        if not 1 <= args.limit <= 10:
            raise ValueError("SEC recent filings limit must be between 1 and 10")
        if args.ticker.upper() not in SEC_CIK_BY_TICKER:
            raise ValueError("SEC ticker is not in the bounded three-ticker scaffold")


def _build_sec_request(environ: Mapping[str, str], *, ticker: str, execute: bool) -> RequestSpec:
    user_agent = base.require_secret("SEC_USER_AGENT", dict(environ), execute=execute)
    contact = base.require_secret("SEC_CONTACT_EMAIL", dict(environ), execute=execute)
    cik = SEC_CIK_BY_TICKER[ticker.upper()]
    return RequestSpec(
        provider="sec_edgar",
        method="GET",
        url=f"https://data.sec.gov/submissions/CIK{cik}.json",
        params={},
        headers={"Accept": "application/json", "User-Agent": f"{user_agent} {contact}"},
        json_body=None,
        secret_values=(user_agent, contact),
        item_path=("filings", "recent"),
        columnar_items=True,
        required_any_item_fields=frozenset(
            {"accessionNumber", "filingDate", "form", "primaryDocument"}
        ),
        rate_limit_headers=(),
    )


def build_request(
    args: argparse.Namespace, environ: Mapping[str, str]
) -> tuple[RequestSpec, dict[str, str]]:
    if args.provider == "marketaux":
        return (
            marketaux.build_request(
                environ,
                query=args.query,
                limit=args.limit,
                execute=args.execute,
            ),
            {"query": args.query},
        )
    if args.provider == "finnhub":
        return (
            finnhub.build_request(
                environ,
                symbol=args.symbol.upper(),
                execute=args.execute,
            ),
            {"symbol": args.symbol.upper()},
        )
    if args.provider == "eia":
        return (
            eia.build_request(
                environ,
                dataset=args.dataset,
                limit=args.limit,
                execute=args.execute,
            ),
            {"dataset": args.dataset},
        )
    return (
        _build_sec_request(environ, ticker=args.ticker, execute=args.execute),
        {"ticker": args.ticker.upper()},
    )


def _truncate_sec_body(payload: object, *, limit: int) -> object:
    if not isinstance(payload, dict):
        return payload
    filings = payload.get("filings")
    if not isinstance(filings, dict):
        return payload
    recent = filings.get("recent")
    if not isinstance(recent, dict):
        return payload
    truncated = {
        key: value[:limit] if isinstance(value, list) else value for key, value in recent.items()
    }
    return {**payload, "filings": {**filings, "recent": truncated}}


def _truncate_list_body(payload: object, *, provider: str, limit: int) -> object:
    if not isinstance(payload, dict):
        return payload
    if provider == "marketaux" and isinstance(payload.get("data"), list):
        return {**payload, "data": payload["data"][:limit]}
    if provider == "eia":
        response = payload.get("response")
        if isinstance(response, dict) and isinstance(response.get("data"), list):
            return {**payload, "response": {**response, "data": response["data"][:limit]}}
    return payload


def _contains_secret_marker(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in SECRET_QUERY_MARKERS)


def _sanitize_provider_payload(payload: object) -> object:
    if isinstance(payload, dict):
        sanitized: dict[str, object] = {}
        for key, value in payload.items():
            if str(key).lower() in FORBIDDEN_RESPONSE_KEYS:
                continue
            if isinstance(value, str) and _contains_secret_marker(value):
                continue
            sanitized[str(key)] = _sanitize_provider_payload(value)
        return sanitized
    if isinstance(payload, list):
        return [
            _sanitize_provider_payload(value)
            for value in payload
            if not (isinstance(value, str) and _contains_secret_marker(value))
        ]
    return payload


def _has_secret_risk(payload: object) -> bool:
    if isinstance(payload, dict):
        return any(
            str(key).lower() in FORBIDDEN_RESPONSE_KEYS
            or (isinstance(value, str) and _contains_secret_marker(value))
            or _has_secret_risk(value)
            for key, value in payload.items()
        )
    if isinstance(payload, list):
        return any(_has_secret_risk(value) for value in payload)
    return isinstance(payload, str) and _contains_secret_marker(payload)


def sanitize_response_body(payload: object, *, provider: str, limit: int) -> object:
    payload = _sanitize_provider_payload(payload)
    if provider == "sec_edgar":
        return _truncate_sec_body(payload, limit=limit)
    return _truncate_list_body(payload, provider=provider, limit=limit)


def _safe_response_headers(headers: httpx.Headers) -> dict[str, str]:
    return {
        name.lower(): value
        for name, value in headers.items()
        if name.lower() in SAFE_RESPONSE_HEADERS
    }


def _capture_path(provider: str, captured_at: datetime) -> Path:
    stamp = captured_at.strftime("%Y%m%dT%H%M%S%fZ")
    return CAPTURE_ROOT / f"{provider}_{stamp}.json"


def _ensure_capture_path(path: Path) -> Path:
    root = CAPTURE_ROOT.resolve()
    resolved = path.resolve()
    if resolved.parent != root:
        raise ValueError("capture output must be directly below local raw capture directory")
    return resolved


def _non_secret_params(request: RequestSpec) -> dict[str, str | int]:
    return {
        name: value
        for name, value in request.params.items()
        if name.lower() not in SECRET_PARAM_NAMES
    }


def create_capture(
    request: RequestSpec,
    *,
    context: dict[str, str],
    limit: int,
    transport: httpx.BaseTransport | None = None,
    captured_at: datetime | None = None,
) -> dict[str, object]:
    now = captured_at or datetime.now(UTC)
    with httpx.Client(timeout=20.0, transport=transport, follow_redirects=False) as client:
        response = client.request(
            request.method,
            request.url,
            params=request.params,
            headers=request.headers,
            json=request.json_body,
        )
    try:
        payload: object = response.json()
    except ValueError as exc:
        raise ValueError("provider response is not JSON; capture not written") from exc
    body = sanitize_response_body(payload, provider=request.provider, limit=limit)
    if _has_secret_risk(body):
        raise ValueError("provider response could not be sanitized; capture not written")
    capture: dict[str, object] = {
        "capture_version": CAPTURE_VERSION,
        "provider": request.provider,
        "captured_at": now.isoformat(),
        "endpoint_family": request.url,
        "request": {
            "method": request.method,
            "non_secret_params": _non_secret_params(request),
            "secret_param_names": sorted(
                name for name in request.params if name.lower() in SECRET_PARAM_NAMES
            ),
            "secret_header_names": sorted(
                name for name in request.headers if name.lower() in SECRET_HEADER_NAMES
            ),
            "context": context,
            "limit": limit,
        },
        "http_status": response.status_code,
        "safe_response_headers": _safe_response_headers(response.headers),
        "response_body": body,
    }
    path = _ensure_capture_path(_capture_path(request.provider, now))
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(capture, ensure_ascii=False, sort_keys=True, indent=2).encode()
    path.write_bytes(encoded)
    try:
        display_path = str(path.relative_to(REPOSITORY_ROOT))
    except ValueError:
        display_path = str(path)
    return {
        "provider": request.provider,
        "capture_file": display_path,
        "file_size_bytes": len(encoded),
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "http_status": response.status_code,
        "saved": True,
    }


def _dry_run_report(request: RequestSpec, context: dict[str, str], limit: int) -> dict[str, object]:
    return {
        "provider": request.provider,
        "endpoint_family": request.url,
        "method": request.method,
        "context_fields": sorted(context),
        "limit": limit,
        "execute": False,
        "saved": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        _validate_args(args)
        environ = load_environment(args.env_file)
        request, context = build_request(args, environ)
    except (MissingCredentialError, ValueError):
        print(json.dumps({"provider": args.provider, "status": "BLOCKED"}, sort_keys=True))
        return 2
    if not args.execute:
        print(json.dumps(_dry_run_report(request, context, args.limit), sort_keys=True))
        return 0
    try:
        report = create_capture(request, context=context, limit=args.limit)
    except (httpx.HTTPError, ValueError):
        print(json.dumps({"provider": args.provider, "status": "BLOCKED"}, sort_keys=True))
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
