"""Authority-neutral M2-C Event Evidence Bundle reconciliation task."""

from __future__ import annotations

import asyncio
import dataclasses
from datetime import timedelta

from market_intelligence.core.config import Settings
from market_intelligence.db.session import create_engine, create_session_factory
from market_intelligence.event_evidence.worker import EventEvidenceBundleWorker
from market_intelligence.tasks.celery_app import celery_app


async def _process(settings: Settings) -> dict[str, int]:
    engine = create_engine(settings)
    try:
        report = await EventEvidenceBundleWorker(
            create_session_factory(engine),
            max_attempts=settings.EVENT_BUNDLE_MAX_ATTEMPTS,
            stale_after=timedelta(seconds=settings.EVENT_BUNDLE_STALE_AFTER_SECONDS),
        ).process_batch(limit=settings.EVENT_BUNDLE_BATCH_LIMIT)
        return dataclasses.asdict(report)
    finally:
        await engine.dispose()


@celery_app.task(name="event_evidence.reconcile")  # type: ignore[untyped-decorator]
def reconcile_event_evidence_bundles() -> dict[str, int]:
    return asyncio.run(_process(Settings(_env_file=None)))  # type: ignore[call-arg]
