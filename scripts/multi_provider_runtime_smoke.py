#!/usr/bin/env python3
"""Unified Finnhub/EIA/SEC runtime smoke; default is inert dry-run."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Sequence

from market_intelligence.pipeline.multi_provider_verification import (
    VerificationMode,
    run_multi_provider_verification,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run bounded multi-provider verification")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--doctor", action="store_true")
    mode.add_argument("--bootstrap-target", action="store_true")
    mode.add_argument("--execute", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    mode = VerificationMode.DRY_RUN
    if args.doctor:
        mode = VerificationMode.DOCTOR
    elif args.bootstrap_target:
        mode = VerificationMode.BOOTSTRAP
    elif args.execute:
        mode = VerificationMode.EXECUTE
    # Only explicit execute receives the process environment. Other modes
    # cannot inspect provider credentials through this boundary.
    environ = os.environ if mode is VerificationMode.EXECUTE else {}
    report, code = asyncio.run(run_multi_provider_verification(mode, environ))
    print(json.dumps(report.safe_dict(), sort_keys=True, separators=(",", ":")))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
