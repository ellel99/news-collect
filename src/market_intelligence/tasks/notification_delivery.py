from __future__ import annotations

import asyncio
import os
from typing import Any

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
        token, chat_id = (
            os.environ.get("TELEGRAM_BOT_TOKEN", ""),
            os.environ.get("TELEGRAM_CHAT_ID", ""),
        )
        credential = TelegramRuntimeCredential(token, chat_id) if token and chat_id else None
        engine = create_engine(get_settings())
        try:
            report = await NotificationDeliveryService(
                create_session_factory(engine), HttpxTelegramTransport()
            ).deliver(credential, limit=limit)
            return {
                "status": report.status,
                "sent": report.sent,
                "failed": report.failed,
                "safe_errors": list(report.safe_errors),
            }
        finally:
            await engine.dispose()

    return asyncio.run(run())
