"""Explicit process-environment wiring for SPEC-0038 scheduler execution."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from market_intelligence.collection.contracts import CollectionTarget
from market_intelligence.core.config import Settings
from market_intelligence.db.models import CollectionRun
from market_intelligence.db.session import create_engine, create_session_factory
from market_intelligence.evidence.end_to_end import EndToEndOutcome, EndToEndStatus
from market_intelligence.feed.provider_feed import ProviderFeedService
from market_intelligence.pipeline.marketaux_real_collection import (
    MarketauxRealCollectionPipeline,
    resolve_marketaux_target,
)
from market_intelligence.pipeline.multi_provider_ingestion import (
    MultiProviderIngestionPipeline,
    resolve_provider_target,
)
from market_intelligence.providers.contracts import ProviderAdapter
from market_intelligence.providers.credentials import RuntimeCredential
from market_intelligence.providers.eia import EiaAdapter
from market_intelligence.providers.finnhub import FinnhubAdapter
from market_intelligence.providers.http_transport import HttpxProviderTransport
from market_intelligence.providers.sec_edgar import SecEdgarAdapter
from market_intelligence.scheduler.multi_provider import (
    PROVIDER_ORDER,
    MultiProviderScheduleSummary,
    MultiProviderTelegramScheduler,
    ProviderCycleResult,
    ProviderScheduleReport,
    ProviderScheduleStatus,
    ReliableProviderNotificationService,
)
from market_intelligence.telegram.manual_push import (
    HttpxTelegramTransport,
    TelegramRuntimeCredential,
)


class _RunnablePipeline(Protocol):
    async def run(self, target: CollectionTarget) -> EndToEndOutcome: ...


_RETRYABLE_COLLECTION_ERRORS = frozenset(
    {
        "COLLECTION_TIMEOUT",
        "COLLECTION_NETWORK",
        "COLLECTION_RATE_LIMITED",
        "COLLECTION_UPSTREAM_5XX",
        "COLLECTION_UPSTREAM_RETRYABLE",
        "COLLECTION_DATABASE_UNAVAILABLE",
        "COLLECTION_LOCK_LOST",
    }
)


async def _collection_error_code(
    factory: async_sessionmaker[AsyncSession], outcome: EndToEndOutcome
) -> str | None:
    if outcome.collection_run_id is None:
        return None
    async with factory() as session:
        return await session.scalar(
            select(CollectionRun.error_code).where(CollectionRun.id == outcome.collection_run_id)
        )


def dry_run_summary() -> MultiProviderScheduleSummary:
    return MultiProviderScheduleSummary(
        "DRY_RUN",
        tuple(
            ProviderScheduleReport(provider, "DRY_RUN", "not_started", 0, 0, 0, 0, 0)
            for provider in PROVIDER_ORDER
        ),
    )


async def run_multi_provider_scheduler_cycle(
    *, execute: bool, environ: Mapping[str, str], limit: int = 1
) -> MultiProviderScheduleSummary:
    if not execute:
        return dry_run_summary()
    telegram_token = environ.get("TELEGRAM_BOT_TOKEN", "")
    telegram_chat = environ.get("TELEGRAM_CHAT_ID", "")
    if not telegram_token or not telegram_chat:
        return MultiProviderScheduleSummary(
            "BLOCKED",
            tuple(
                ProviderScheduleReport(
                    provider,
                    "BLOCKED",
                    "not_started",
                    0,
                    0,
                    0,
                    0,
                    0,
                    ("telegram_runtime_credential_missing",),
                )
                for provider in PROVIDER_ORDER
            ),
        )
    settings = Settings(COLLECTION_BATCH_LIMIT=limit, _env_file=None)  # type: ignore[call-arg]
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)
    transport = HttpxProviderTransport()
    feed = ProviderFeedService(factory)
    try:
        executors = {}
        credentials = _credentials(environ)
        cadences = {
            "marketaux": settings.MARKETAUX_CADENCE_SECONDS,
            "finnhub": settings.FINNHUB_CADENCE_SECONDS,
            "eia": settings.EIA_CADENCE_SECONDS,
            "sec_edgar": settings.SEC_EDGAR_CADENCE_SECONDS,
        }
        for provider in PROVIDER_ORDER:
            credential = credentials.get(provider)
            if credential is None:
                continue
            claimed = await redis.set(
                f"scheduler:cadence:{provider}", "1", nx=True, ex=cadences[provider]
            )
            if not claimed:

                async def not_due(provider: str = provider) -> ProviderCycleResult:
                    return ProviderCycleResult(
                        provider, ProviderScheduleStatus.NO_NEW_ITEMS, "not_due"
                    )

                executors[provider] = not_due
                continue
            if provider == "marketaux":
                resolved = await resolve_marketaux_target(factory)
                if resolved is None:
                    continue
                pipeline: _RunnablePipeline = MarketauxRealCollectionPipeline(
                    factory, redis, settings, credential, transport
                )
                target = resolved.target
            else:
                resolved_provider = await resolve_provider_target(factory, provider)
                if resolved_provider is None:
                    continue
                if provider == "finnhub":
                    adapter: ProviderAdapter = FinnhubAdapter(credential)
                elif provider == "eia":
                    adapter = EiaAdapter(credential)
                else:
                    adapter = SecEdgarAdapter(credential)
                pipeline = MultiProviderIngestionPipeline(
                    factory, redis, settings, adapter, transport
                )
                target = resolved_provider.target

            async def execute_one(
                provider: str = provider,
                pipeline: _RunnablePipeline = pipeline,
                target: CollectionTarget = target,
            ) -> ProviderCycleResult:
                outcome = await pipeline.run(target)
                evidence_count = sum(
                    1
                    for trigger in outcome.trigger_outcomes
                    if trigger.pipeline_outcome is not None
                    and trigger.pipeline_outcome.evidence_item_id is not None
                )
                items = (
                    await feed.for_run(outcome.collection_run_id, provider, 5)
                    if outcome.collection_run_id is not None
                    else ()
                )
                if outcome.status is not EndToEndStatus.PROCESSED:
                    collection_error = await _collection_error_code(factory, outcome)
                    return ProviderCycleResult(
                        provider,
                        (
                            ProviderScheduleStatus.RETRY
                            if collection_error in _RETRYABLE_COLLECTION_ERRORS
                            else ProviderScheduleStatus.FAILED
                        ),
                        outcome.status.value,
                        outcome.raw_item_count,
                        evidence_count,
                        len(items),
                        items,
                        (
                            (collection_error.lower(),)
                            if collection_error is not None
                            else tuple(error.code for error in outcome.safe_errors)
                            or ("collection_not_succeeded",)
                        ),
                    )
                status = (
                    ProviderScheduleStatus.PASS
                    if outcome.raw_item_count > 0
                    else ProviderScheduleStatus.NO_NEW_ITEMS
                )
                return ProviderCycleResult(
                    provider,
                    status,
                    "processed" if outcome.raw_item_count else "no_new_items",
                    outcome.raw_item_count,
                    evidence_count,
                    len(items),
                    items,
                )

            executors[provider] = execute_one
        notifications = ReliableProviderNotificationService(
            factory,
            TelegramRuntimeCredential(telegram_token, telegram_chat),
            HttpxTelegramTransport(),
        )
        return await MultiProviderTelegramScheduler(executors, notifications).run()
    finally:
        await redis.aclose()
        await engine.dispose()


def _credentials(environ: Mapping[str, str]) -> dict[str, RuntimeCredential]:
    result: dict[str, RuntimeCredential] = {}
    values = {
        "marketaux": ("MARKETAUX_API_TOKEN", environ.get("MARKETAUX_API_TOKEN", "")),
        "finnhub": ("FINNHUB_API_KEY", environ.get("FINNHUB_API_KEY", "")),
        "eia": ("EIA_API_KEY", environ.get("EIA_API_KEY", "")),
    }
    for provider, (name, value) in values.items():
        if value:
            result[provider] = RuntimeCredential(name, value)
    agent = environ.get("SEC_USER_AGENT", "")
    contact = environ.get("SEC_CONTACT_EMAIL", "")
    if agent and contact:
        result["sec_edgar"] = RuntimeCredential("SEC_USER_AGENT", f"{agent} {contact}")
    return result
