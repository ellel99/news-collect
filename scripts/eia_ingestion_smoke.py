#!/usr/bin/env python3
"""Bounded EIA ingestion smoke; inert unless --execute is explicit."""

import argparse
import asyncio
import json
import os

from market_intelligence.pipeline.provider_runtime import (
    dry_run_summary,
    execute_provider,
    inspect_provider_target,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true")
    mode.add_argument("--doctor", action="store_true")
    mode.add_argument("--bootstrap-target", action="store_true")
    parser.add_argument("--dataset", default="electricity")
    parser.add_argument("--limit", type=int, default=1)
    args = parser.parse_args()
    if args.doctor or args.bootstrap_target:
        report, code = asyncio.run(inspect_provider_target("eia", bootstrap=args.bootstrap_target))
        print(json.dumps(report, sort_keys=True))
        return code
    if not 1 <= args.limit <= 5:
        print(
            json.dumps(
                {
                    **dry_run_summary("eia", args.limit),
                    "status": "BLOCKED",
                    "safe_errors": ["provider_limit_invalid"],
                },
                sort_keys=True,
            )
        )
        return 2
    if not args.execute:
        print(json.dumps(dry_run_summary("eia", args.limit), sort_keys=True))
        return 0
    report, code = asyncio.run(
        execute_provider("eia", args.limit, os.environ, {"dataset": args.dataset})
    )
    print(json.dumps(report, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
