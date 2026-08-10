#!/usr/bin/env python3
"""Guarded, head-only Alembic version repair; default is dry-run."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from pathlib import Path

from market_intelligence.core.config import Settings
from market_intelligence.db.alembic_state import load_migration_inventory, repair_alembic_state
from market_intelligence.db.session import create_engine


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Safely assess or repair Alembic state")
    parser.add_argument("--execute", action="store_true")
    return parser


async def run(*, execute: bool) -> tuple[dict[str, object], int]:
    root = Path(__file__).resolve().parents[1]
    inventory = load_migration_inventory(root)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    engine = create_engine(settings)
    try:
        report = await repair_alembic_state(engine, inventory, execute=execute)
    except Exception:
        return {
            "status": "BLOCKED",
            "execute_requested": execute,
            "database_updated": False,
            "safe_errors": ["alembic_repair_failed"],
        }, 2
    finally:
        await engine.dispose()
    return report.safe_dict(), 0 if report.status in ("PASS", "DRY_RUN", "REPAIRED") else 2


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report, exit_code = asyncio.run(run(execute=args.execute))
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
