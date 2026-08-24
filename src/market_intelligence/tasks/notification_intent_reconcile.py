from __future__ import annotations

import asyncio

from market_intelligence.collection.control_plane_tools import authority_is_approved
from market_intelligence.core.config import get_settings
from market_intelligence.db.session import create_engine, create_session_factory
from market_intelligence.notifications.intent import NotificationIntentReconciler
from market_intelligence.tasks.celery_app import celery_app


@celery_app.task(name="notification.intent.reconcile")  # type: ignore[untyped-decorator]
def reconcile_notification_intents(limit: int = 100) -> dict[str, int]:
    async def run() -> dict[str, int]:
        engine = create_engine(get_settings())
        try:
            factory = create_session_factory(engine)
            async with factory() as session:
                if not await authority_is_approved(session):
                    return {"scanned": 0, "created": 0, "resolved": 0}
            report = await NotificationIntentReconciler(factory).reconcile(limit=limit)
            return {
                "scanned": report.scanned,
                "created": report.created,
                "resolved": report.resolved,
            }
        finally:
            await engine.dispose()

    return asyncio.run(run())
