#!/usr/bin/env python3
"""Diagnose Alembic code/database state without exposing configuration."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from market_intelligence.core.config import Settings
from market_intelligence.db.alembic_state import diagnose_alembic_state, load_migration_inventory
from market_intelligence.db.session import create_engine


async def run() -> tuple[dict[str, object], int]:
    root = Path(__file__).resolve().parents[1]
    inventory = load_migration_inventory(root)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    engine = create_engine(settings)
    try:
        async with engine.connect() as connection:
            report = await diagnose_alembic_state(connection, inventory)
    except Exception:
        return {"status": "BLOCKED", "safe_errors": ["alembic_doctor_failed"]}, 2
    finally:
        await engine.dispose()
    return report.safe_dict(), 0 if report.status == "PASS" else 2


def main() -> int:
    report, exit_code = asyncio.run(run())
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
