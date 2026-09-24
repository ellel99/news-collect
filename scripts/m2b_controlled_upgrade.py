#!/usr/bin/env python3
"""Only supported deployment entry for production upgrade 0009 -> 0010."""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json

from market_intelligence.core.config import Settings
from market_intelligence.db.session import create_engine
from market_intelligence.rich_evidence.migration_gate import controlled_upgrade_0010


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--writers-stopped", action="store_true")
    return parser.parse_args()


async def _run(args: argparse.Namespace) -> tuple[dict[str, object], int]:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    engine = create_engine(settings)
    try:
        report = await controlled_upgrade_0010(
            engine,
            execute=args.execute,
            writers_stopped=args.writers_stopped,
        )
        payload = dataclasses.asdict(report)
        return payload, 0 if report.status in {"PASS", "DRY_RUN"} else 2
    finally:
        await engine.dispose()


def main() -> int:
    payload, code = asyncio.run(_run(_arguments()))
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
