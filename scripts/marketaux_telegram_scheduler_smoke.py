#!/usr/bin/env python3
"""Run one safe scheduler cycle; default is inert dry-run."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Sequence

from market_intelligence.scheduler.runtime import run_scheduler_cycle


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one Marketaux Telegram scheduler cycle")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--limit", type=int, default=1)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    summary = asyncio.run(
        run_scheduler_cycle(execute=args.execute, limit=args.limit, environ=os.environ)
    )
    print(json.dumps(summary.safe_dict(), sort_keys=True, separators=(",", ":")))
    return 0 if summary.status in ("PASS", "DRY_RUN", "NO_NEW_ITEMS") else 2


if __name__ == "__main__":
    raise SystemExit(main())
