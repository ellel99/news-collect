"""Celery entry point for idempotent Event processing without AI execution."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

from market_intelligence.core.config import Settings
from market_intelligence.db.session import create_engine, create_session_factory
from market_intelligence.event_intelligence.runtime import EventProcessingRuntime
from market_intelligence.tasks.celery_app import celery_app


class CeleryEvidenceEventEnqueuer:
    """Post-commit enqueue with deterministic task identity."""

    def enqueue(self, evidence_item_id: UUID) -> None:
        process_evidence_task.apply_async(
            args=(str(evidence_item_id),),
            task_id=f"event-evidence-{evidence_item_id}",
        )


@celery_app.task(name="event_intelligence.process_evidence")  # type: ignore[untyped-decorator]
def process_evidence_task(evidence_item_id: str) -> dict[str, Any]:
    return asyncio.run(_process(UUID(evidence_item_id)))


async def _process(evidence_item_id: UUID) -> dict[str, Any]:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            outcome = await EventProcessingRuntime().process_evidence(session, evidence_item_id)
            await session.commit()
        return {
            "status": outcome.status.value,
            "event_candidate_present": outcome.event_candidate_id is not None,
            "fact_built": outcome.fact_snapshot_hash is not None,
            "analysis_written": False,
            "safe_errors": list(outcome.safe_errors),
        }
    finally:
        await engine.dispose()
