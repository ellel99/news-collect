from __future__ import annotations

import asyncio
import os
from typing import Any

from market_intelligence.collection.control_plane_tools import authority_is_approved
from market_intelligence.core.config import get_settings
from market_intelligence.db.session import create_engine, create_session_factory
from market_intelligence.notifications.delivery import NotificationDeliveryService
from market_intelligence.tasks.celery_app import celery_app
from market_intelligence.telegram.manual_push import (
    HttpxTelegramTransport,
    TelegramRuntimeCredential,
)


@celery_app.task(name="notification.telegram.deliver")  # type: ignore[untyped-decorator]
def deliver_notifications(limit: int = 20) -> dict[str, Any]:
    async def run() -> dict[str, Any]:
        engine = create_engine(get_settings())
        factory = create_session_factory(engine)
        try:
            async with factory() as session:
                if not await authority_is_approved(session):
                    return {
                        "status": "BLOCKED",
                        "sent": 0,
                        "failed": 0,
                        "safe_errors": ["authority_activation_not_approved"],
                    }
            token, chat_id = (
                os.environ.get("TELEGRAM_BOT_TOKEN", ""),
                os.environ.get("TELEGRAM_CHAT_ID", ""),
            )
            credential = TelegramRuntimeCredential(token, chat_id) if token and chat_id else None
            report = await NotificationDeliveryService(factory, HttpxTelegramTransport()).deliver(
                credential, limit=limit
            )
            return {
                "status": report.status,
                "sent": report.sent,
                "failed": report.failed,
                "safe_errors": list(report.safe_errors),
            }
        finally:
            await engine.dispose()

    return asyncio.run(run())
