#!/usr/bin/env python3
"""Run the value-free typed preflight required before Alembic revision 0010."""

from __future__ import annotations

import asyncio
import json

from market_intelligence.core.config import Settings
from market_intelligence.db.session import create_engine
from market_intelligence.rich_evidence.migration_preflight import validate_0010_pre_migration


async def run() -> tuple[dict[str, object], int]:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    engine = create_engine(settings)
    try:
        return await validate_0010_pre_migration(engine)
    finally:
        await engine.dispose()


def main() -> int:
    report, exit_code = asyncio.run(run())
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
