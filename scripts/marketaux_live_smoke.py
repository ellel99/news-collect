#!/usr/bin/env python3
"""Bounded Marketaux live smoke harness; dry-run is always the default."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID

from market_intelligence.providers.contracts import ProviderFetchRequest, ProviderTransport
from market_intelligence.providers.credentials import RuntimeCredential
from market_intelligence.providers.http_transport import HttpxProviderTransport
from market_intelligence.providers.marketaux_real import MarketauxRealAdapter

_PROVIDER = "marketaux"
_MAX_LIMIT = 3


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a bounded Marketaux adapter smoke")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--query", default="technology")
    return parser


def _dry_run_report(limit: int) -> dict[str, object]:
    return {
        "provider": _PROVIDER,
        "mode": "dry_run",
        "status": "DRY_RUN",
        "limit": limit,
        "endpoint_family": "marketaux_news_all",
        "request_enabled": False,
        "credential_read": False,
        "response_saved": False,
    }


def _blocked_report(code: str, limit: int) -> dict[str, object]:
    return {
        "provider": _PROVIDER,
        "mode": "execute",
        "status": "BLOCKED",
        "limit": limit,
        "item_count": 0,
        "safe_errors": [code],
        "response_saved": False,
    }


async def run_smoke(
    *,
    execute: bool,
    limit: int,
    query: str,
    environ: Mapping[str, str] | None = None,
    transport: ProviderTransport | None = None,
) -> tuple[dict[str, object], int]:
    if not 1 <= limit <= _MAX_LIMIT:
        return _blocked_report("provider_record_limit_invalid", limit), 2
    if not execute:
        return _dry_run_report(limit), 0

    runtime_environment = os.environ if environ is None else environ
    token = runtime_environment.get("MARKETAUX_API_TOKEN", "")
    if not token:
        return _blocked_report("provider_runtime_credential_missing", limit), 2

    adapter = MarketauxRealAdapter(RuntimeCredential("MARKETAUX_API_TOKEN", token))
    runtime_transport = transport or HttpxProviderTransport()
    result = await adapter.fetch(
        ProviderFetchRequest(
            source_id=UUID(int=0),
            source_account_id=None,
            cursor=None,
            config={"query": query, "timeout_seconds": 10},
            limit=limit,
            deadline_at=datetime.now(UTC) + timedelta(seconds=30),
            correlation_id="manual-marketaux-bounded-smoke",
        ),
        runtime_transport,
    )
    if result.safe_errors:
        report = _blocked_report(result.safe_errors[0].code.value, limit)
        error = result.safe_errors[0]
        report["retryable"] = error.retryable
        report["retry_after_present"] = error.retry_after_seconds is not None
        return report, 3
    return (
        {
            "provider": _PROVIDER,
            "mode": "execute",
            "status": "PASS",
            "limit": limit,
            "item_count": len(result.raw_items),
            "has_more": result.has_more,
            "cursor_present": result.next_cursor is not None,
            "safe_errors": [],
            "response_saved": False,
        },
        0,
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    transport: ProviderTransport | None = None,
) -> int:
    args = _parser().parse_args(argv)
    report, exit_code = asyncio.run(
        run_smoke(
            execute=bool(args.execute),
            limit=int(args.limit),
            query=str(args.query),
            environ=environ,
            transport=transport,
        )
    )
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
