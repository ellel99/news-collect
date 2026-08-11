"""Explicit process-environment wiring for SPEC-0038 scheduler execution."""

from __future__ import annotations

import json
import math
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
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
    PendingProviderNotificationService,
    ProviderCycleResult,
    ProviderExecutor,
    ProviderScheduleReport,
    ProviderScheduleStatus,
    ReliableProviderNotificationService,
)
from market_intelligence.telegram.manual_push import (
    HttpxTelegramTransport,
    TelegramRuntimeCredential,
)


class _RunnablePipeline(Protocol):
    async def run(
        self,
        target: CollectionTarget,
        *,
        collection_run_id: uuid.UUID | None = None,
        attempt: int = 0,
    ) -> EndToEndOutcome: ...


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


def collection_schedule_status(error_code: str | None) -> ProviderScheduleStatus:
    return (
        ProviderScheduleStatus.RETRY
        if error_code in _RETRYABLE_COLLECTION_ERRORS
        else ProviderScheduleStatus.FAILED
    )


@dataclass(frozen=True, slots=True)
class ProviderCadenceClaim:
    status: str
    collection_run_id: uuid.UUID | None = None
    attempt: int = 0


class ProviderCadenceController:
    """Keep normal cadence and bounded collection retry gates independent."""

    def __init__(self, redis: Redis, max_retry_delay: int) -> None:
        self._redis = redis
        self._max_retry_delay = max_retry_delay

    async def claim(self, provider: str, cadence_seconds: int) -> ProviderCadenceClaim:
        if await self._redis.exists(self._retry_key(provider)):
            return ProviderCadenceClaim("retry_wait")
        context = await self._redis.get(self._retry_context_key(provider))
        claimed = await self._redis.set(
            self._cadence_key(provider), "1", nx=True, ex=cadence_seconds
        )
        if not claimed:
            return ProviderCadenceClaim("not_due")
        if context is None:
            return ProviderCadenceClaim("claimed")
        await self._redis.delete(self._retry_context_key(provider))
        try:
            value = json.loads(context)
            run_id = uuid.UUID(value["collection_run_id"])
            attempt = int(value["attempt"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return ProviderCadenceClaim("blocked")
        if attempt < 1:
            return ProviderCadenceClaim("blocked")
        return ProviderCadenceClaim("claimed", run_id, attempt)

    async def schedule_retry(
        self,
        provider: str,
        retry_delay: float,
        collection_run_id: uuid.UUID,
        attempt: int,
    ) -> int:
        delay = max(1, min(math.ceil(retry_delay), self._max_retry_delay))
        context = json.dumps(
            {"collection_run_id": str(collection_run_id), "attempt": attempt},
            sort_keys=True,
            separators=(",", ":"),
        )
        async with self._redis.pipeline(transaction=True) as pipeline:
            pipeline.delete(self._cadence_key(provider))
            pipeline.set(self._retry_key(provider), "1", ex=delay)
            pipeline.set(
                self._retry_context_key(provider),
                context,
                ex=self._max_retry_delay + 86400,
            )
            await pipeline.execute()
        return delay

    @staticmethod
    def _cadence_key(provider: str) -> str:
        return f"scheduler:cadence:{provider}"

    @staticmethod
    def _retry_key(provider: str) -> str:
        return f"scheduler:retry:{provider}"

    @staticmethod
    def _retry_context_key(provider: str) -> str:
        return f"scheduler:retry-context:{provider}"


async def _collection_retry_state(
    factory: async_sessionmaker[AsyncSession], outcome: EndToEndOutcome
) -> tuple[str | None, int]:
    if outcome.collection_run_id is None:
        return None, 0
    async with factory() as session:
        row = (
            await session.execute(
                select(CollectionRun.error_code, CollectionRun.retry_count).where(
                    CollectionRun.id == outcome.collection_run_id
                )
            )
        ).one_or_none()
    return (None, 0) if row is None else (row.error_code, row.retry_count)


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
    settings = Settings(COLLECTION_BATCH_LIMIT=limit, _env_file=None)  # type: ignore[call-arg]
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)
    transport = HttpxProviderTransport()
    feed = ProviderFeedService(factory)
    cadence = ProviderCadenceController(redis, settings.COLLECTION_MAX_RETRY_AFTER_SECONDS)
    try:
        executors: dict[str, ProviderExecutor] = {}
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
            claim = await cadence.claim(provider, cadences[provider])
            if claim.status != "claimed":

                async def not_due(
                    provider: str = provider,
                    claim_status: str = claim.status,
                ) -> ProviderCycleResult:
                    return ProviderCycleResult(
                        provider,
                        (
                            ProviderScheduleStatus.RETRY
                            if claim_status == "retry_wait"
                            else ProviderScheduleStatus.BLOCKED
                            if claim_status == "blocked"
                            else ProviderScheduleStatus.NO_NEW_ITEMS
                        ),
                        claim_status,
                        safe_errors=("provider_retry_context_invalid",)
                        if claim_status == "blocked"
                        else (),
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
                claim: ProviderCadenceClaim = claim,
            ) -> ProviderCycleResult:
                outcome = await pipeline.run(
                    target,
                    collection_run_id=claim.collection_run_id,
                    attempt=claim.attempt,
                )
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
                    collection_error, retry_count = await _collection_retry_state(factory, outcome)
                    status = collection_schedule_status(collection_error)
                    retry_delay = outcome.retry_delay
                    if status is ProviderScheduleStatus.RETRY:
                        if outcome.collection_run_id is None or retry_count < 1:
                            status = ProviderScheduleStatus.FAILED
                        else:
                            await cadence.schedule_retry(
                                provider,
                                retry_delay
                                if retry_delay is not None
                                else settings.COLLECTION_RETRY_BASE_SECONDS,
                                outcome.collection_run_id,
                                retry_count,
                            )
                    return ProviderCycleResult(
                        provider,
                        status,
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
                        retry_delay_seconds=retry_delay,
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
        notifications = (
            ReliableProviderNotificationService(
                factory,
                TelegramRuntimeCredential(telegram_token, telegram_chat),
                HttpxTelegramTransport(),
            )
            if telegram_token and telegram_chat
            else PendingProviderNotificationService(factory)
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
