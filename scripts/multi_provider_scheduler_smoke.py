#!/usr/bin/env python3
"""Run one multi-provider scheduler cycle; default is completely inert."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Sequence

from market_intelligence.scheduler.multi_provider_runtime import (
    run_multi_provider_scheduler_cycle,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run multi-provider scheduler smoke")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--limit", type=int, default=1, choices=range(1, 4))
    args = parser.parse_args(argv)
    summary = asyncio.run(
        run_multi_provider_scheduler_cycle(
            execute=args.execute,
            environ=os.environ if args.execute else {},
            limit=args.limit,
        )
    )
    print(json.dumps(summary.safe_dict(), sort_keys=True, separators=(",", ":")))
    return 0 if summary.status in {"PASS", "DRY_RUN"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
