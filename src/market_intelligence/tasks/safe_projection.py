"""Authority-neutral Celery reconciliation for durable safe factual projections."""

from __future__ import annotations

import asyncio
from datetime import timedelta

from market_intelligence.core.config import Settings
from market_intelligence.db.session import create_engine, create_session_factory
from market_intelligence.safe_projection.worker import SafeFactProjectionWorker
from market_intelligence.tasks.celery_app import celery_app


async def _process(settings: Settings) -> dict[str, int]:
    engine = create_engine(settings)
    try:
        worker = SafeFactProjectionWorker(
            create_session_factory(engine),
            max_attempts=settings.SAFE_PROJECTION_MAX_ATTEMPTS,
            stale_after=timedelta(seconds=settings.SAFE_PROJECTION_STALE_AFTER_SECONDS),
        )
        report = await worker.process_batch(limit=settings.SAFE_PROJECTION_BATCH_LIMIT)
        return {
            "claimed": report.claimed,
            "ready": report.ready,
            "blocked": report.blocked,
            "recovered": report.recovered,
        }
    finally:
        await engine.dispose()


@celery_app.task(name="safe_projection.validate_pending")  # type: ignore[untyped-decorator]
def validate_pending_safe_projections() -> dict[str, int]:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    return asyncio.run(_process(settings))
