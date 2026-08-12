"""Celery entry point for the unified multi-provider scheduler."""

from __future__ import annotations

import asyncio
import os
from typing import Any

from market_intelligence.core.config import Settings
from market_intelligence.scheduler.multi_provider_runtime import (
    run_multi_provider_scheduler_cycle,
)
from market_intelligence.tasks.celery_app import celery_app


@celery_app.task(name="multi_provider.telegram.run")  # type: ignore[untyped-decorator]
def run_multi_provider_cycle() -> dict[str, Any]:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    summary = asyncio.run(
        run_multi_provider_scheduler_cycle(
            execute=settings.MULTI_PROVIDER_SCHEDULER_EXECUTE,
            environ=os.environ if settings.MULTI_PROVIDER_SCHEDULER_EXECUTE else {},
        )
    )
    return summary.safe_dict()
