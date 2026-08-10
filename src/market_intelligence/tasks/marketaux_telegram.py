"""Celery entry point for one minimal Marketaux Telegram cycle."""

from __future__ import annotations

import asyncio
import os
from typing import Any

from market_intelligence.core.config import Settings
from market_intelligence.scheduler.runtime import run_scheduler_cycle
from market_intelligence.tasks.celery_app import celery_app


@celery_app.task(name="marketaux.telegram.run")  # type: ignore[untyped-decorator]
def run_marketaux_telegram_cycle() -> dict[str, Any]:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    summary = asyncio.run(
        run_scheduler_cycle(
            execute=settings.MARKETAUX_TELEGRAM_SCHEDULER_EXECUTE,
            limit=settings.MARKETAUX_TELEGRAM_SCHEDULER_LIMIT,
            environ=os.environ,
        )
    )
    return summary.safe_dict()
