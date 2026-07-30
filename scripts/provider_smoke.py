#!/usr/bin/env python3
"""Bounded provider preflight CLI.

Dry-run is the default. The command never writes provider responses to disk and
prints only a redacted structural report.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

from market_intelligence.providers.preflight import eia, finnhub, marketaux, newsapi_ai, sec_edgar
from market_intelligence.providers.preflight.base import (
    MissingCredentialError,
    RequestSpec,
    SmokeReport,
    blocked_report,
)

PROVIDERS: dict[str, ModuleType] = {
    "eia": eia,
    "finnhub": finnhub,
    "marketaux": marketaux,
    "newsapi_ai": newsapi_ai,
    "sec_edgar": sec_edgar,
}
ENDPOINTS = {name: str(module.ENDPOINT) for name, module in PROVIDERS.items()}
DEFAULT_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


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


def _read_env_file(path: Path, *, required: bool) -> dict[str, str]:
    if not path.exists():
        if required:
            raise ValueError("the requested environment file does not exist")
        return {}
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").lstrip()
        if "=" not in line:
            raise ValueError(f"invalid environment entry on line {line_number}")
        name, value = line.split("=", 1)
        name = name.strip()
        if not ENV_NAME.fullmatch(name):
            raise ValueError(f"invalid environment name on line {line_number}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[name] = value
    return values


def _load_environment(env_file: Path | None) -> dict[str, str]:
    path = env_file or DEFAULT_ENV_FILE
    values = _read_env_file(path, required=env_file is not None)
    values.update(os.environ)
    return values


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

    report = PROVIDERS[args.provider].execute_minimal_request(request)
    _print_report(report)
    return 0 if report.classified_result == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
