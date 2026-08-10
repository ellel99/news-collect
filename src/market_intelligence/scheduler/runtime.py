"""Explicit process-environment wiring for the minimal scheduler."""

from __future__ import annotations

from collections.abc import Mapping

from redis.asyncio import Redis

from market_intelligence.core.config import Settings
from market_intelligence.db.session import create_engine, create_session_factory
from market_intelligence.pipeline.marketaux_real_collection import (
    MarketauxRealCollectionPipeline,
    resolve_marketaux_target,
)
from market_intelligence.providers.contracts import ProviderTransport
from market_intelligence.providers.credentials import RuntimeCredential
from market_intelligence.providers.http_transport import HttpxProviderTransport
from market_intelligence.scheduler.marketaux_telegram import (
    MarketauxTelegramScheduler,
    SchedulerRunSummary,
)
from market_intelligence.telegram.manual_push import (
    HttpxTelegramTransport,
    TelegramRuntimeCredential,
    TelegramTransport,
)


async def run_scheduler_cycle(
    *,
    execute: bool,
    limit: int,
    environ: Mapping[str, str],
    provider_transport: ProviderTransport | None = None,
    telegram_transport: TelegramTransport | None = None,
) -> SchedulerRunSummary:
    if not 1 <= limit <= 3:
        return _runtime_summary("BLOCKED", ("scheduler_limit_invalid",))
    if not execute:
        return _runtime_summary("DRY_RUN")

    marketaux_token = environ.get("MARKETAUX_API_TOKEN", "")
    telegram_token = environ.get("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id = environ.get("TELEGRAM_CHAT_ID", "")
    if not marketaux_token or not telegram_token or not telegram_chat_id:
        return _runtime_summary("BLOCKED", ("scheduler_runtime_credential_missing",), True)

    settings = Settings(  # type: ignore[call-arg]
        COLLECTION_BATCH_LIMIT=limit,
        _env_file=None,
    )
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        resolved = await resolve_marketaux_target(factory)
        if resolved is None:
            return _runtime_summary("BLOCKED", ("marketaux_target_not_unique",), True)
        collection = MarketauxRealCollectionPipeline(
            factory,
            redis,
            settings,
            RuntimeCredential("MARKETAUX_API_TOKEN", marketaux_token),
            provider_transport or HttpxProviderTransport(),
        )
        scheduler = MarketauxTelegramScheduler(
            factory,
            redis,
            collection,
            TelegramRuntimeCredential(telegram_token, telegram_chat_id),
            telegram_transport or HttpxTelegramTransport(),
        )
        return await scheduler.run(resolved.target, limit=limit)
    finally:
        await redis.aclose()
        await engine.dispose()


def _runtime_summary(
    status: str,
    errors: tuple[str, ...] = (),
    credentials_read: bool = False,
) -> SchedulerRunSummary:
    return SchedulerRunSummary(
        provider="marketaux",
        status=status,
        collection_status="not_started",
        raw_item_count=0,
        evidence_item_count=0,
        content_item_count=0,
        new_notification_count=0,
        retry_notification_count=0,
        retry_exhausted_count=0,
        sent_count=0,
        failed_count=0,
        response_saved=False,
        marketaux_token_read=credentials_read,
        telegram_credential_read=credentials_read,
        safe_errors=errors,
    )
