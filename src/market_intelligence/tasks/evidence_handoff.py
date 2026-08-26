"""Authority-neutral Celery reconciliation for R8-A evidence handoff."""

from __future__ import annotations

import asyncio
from datetime import timedelta

from market_intelligence.core.config import Settings
from market_intelligence.db.session import create_engine, create_session_factory
from market_intelligence.evidence.handoff import EvidenceProjectionHandoffWorker
from market_intelligence.tasks.celery_app import celery_app


async def _process(settings: Settings) -> dict[str, int]:
    engine = create_engine(settings)
    try:
        report = await EvidenceProjectionHandoffWorker(
            create_session_factory(engine),
            max_attempts=settings.EVIDENCE_HANDOFF_MAX_ATTEMPTS,
            stale_after=timedelta(seconds=settings.EVIDENCE_HANDOFF_STALE_AFTER_SECONDS),
        ).process_batch(limit=settings.EVIDENCE_HANDOFF_BATCH_LIMIT)
        return {
            "claimed": report.claimed,
            "linked": report.linked,
            "blocked": report.blocked,
            "retried": report.retried,
            "recovered": report.recovered,
        }
    finally:
        await engine.dispose()


@celery_app.task(name="evidence_projection.handoff_ready")  # type: ignore[untyped-decorator]
def handoff_ready_safe_projections() -> dict[str, int]:
    return asyncio.run(_process(Settings(_env_file=None)))  # type: ignore[call-arg]
