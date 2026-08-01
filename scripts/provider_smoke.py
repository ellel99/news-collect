#!/usr/bin/env python3
"""Bounded provider preflight CLI.

Dry-run is the default. The command never writes provider responses to disk and
prints only a redacted structural report.
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

from market_intelligence.providers.preflight import (
    eia,
    finnhub,
    local_env,
    marketaux,
    newsapi_ai,
    sec_edgar,
)
from market_intelligence.providers.preflight.base import (
    MissingCredentialError,
    RequestSpec,
    SmokeReport,
    blocked_report,
)

DEFAULT_ENV_FILE = local_env.DEFAULT_ENV_FILE


def _read_env_file(path: Path, *, required: bool) -> dict[str, str]:
    return local_env.read_env_file(path, required=required)


def _load_environment(env_file: Path | None) -> dict[str, str]:
    path = env_file or DEFAULT_ENV_FILE
    values = _read_env_file(path, required=env_file is not None)
    values.update(os.environ)
    return values


PROVIDERS: dict[str, ModuleType] = {
    "eia": eia,
    "finnhub": finnhub,
    "marketaux": marketaux,
    "newsapi_ai": newsapi_ai,
    "sec_edgar": sec_edgar,
}
ENDPOINTS = {name: str(module.ENDPOINT) for name, module in PROVIDERS.items()}
EXECUTION_ORDER = ("marketaux", "finnhub", "eia", "sec_edgar")
EXECUTION_ENABLED_PROVIDERS = frozenset(EXECUTION_ORDER)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a redacted provider preflight")
    parser.add_argument("--provider", required=True, choices=sorted(PROVIDERS))
    parser.add_argument("--query", default="technology")
    parser.add_argument("--symbol", default="AAPL")
    parser.add_argument("--dataset", default="electricity")
    parser.add_argument("--ticker", default="AAPL")
    parser.add_argument("--max-results", type=int, default=1)
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--execute", action="store_true")
    return parser


def _build_request(args: argparse.Namespace, environ: dict[str, str]) -> RequestSpec:
    if args.provider == "newsapi_ai":
        return newsapi_ai.build_request(
            environ,
            query=args.query,
            max_results=args.max_results,
            execute=args.execute,
        )
    if args.provider == "marketaux":
        return marketaux.build_request(
            environ,
            query=args.query,
            limit=args.limit,
            execute=args.execute,
        )
    if args.provider == "finnhub":
        return finnhub.build_request(environ, symbol=args.symbol, execute=args.execute)
    if args.provider == "eia":
        return eia.build_request(
            environ,
            dataset=args.dataset,
            limit=args.limit,
            execute=args.execute,
        )
    return sec_edgar.build_request(environ, ticker=args.ticker, execute=args.execute)


def _validate_bounds(args: argparse.Namespace) -> None:
    if not 1 <= args.max_results <= 5:
        raise ValueError("--max-results must be between 1 and 5")
    if not 1 <= args.limit <= 5:
        raise ValueError("--limit must be between 1 and 5")


def _print_report(report: SmokeReport) -> None:
    print(json.dumps(report.as_dict(), sort_keys=True))


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    endpoint = ENDPOINTS[args.provider]
    try:
        _validate_bounds(args)
        environ = _load_environment(args.env_file)
        request = _build_request(args, environ)
    except (MissingCredentialError, ValueError):
        _print_report(blocked_report(args.provider, endpoint))
        return 2

    if not args.execute:
        _print_report(blocked_report(args.provider, request.url))
        return 0
    if args.provider not in EXECUTION_ENABLED_PROVIDERS:
        _print_report(blocked_report(args.provider, request.url))
        return 2

    report = PROVIDERS[args.provider].execute_minimal_request(request)
    _print_report(report)
    return 0 if report.classified_result == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
