"""Safe runtime facade for manual multi-provider ingestion smoke commands."""

from __future__ import annotations

from collections.abc import Mapping

from redis.asyncio import Redis
from sqlalchemy import func, select

from market_intelligence.collection.contracts import CollectionTarget
from market_intelligence.core.config import Settings
from market_intelligence.db.models import ContentItem, RawItem
from market_intelligence.db.session import create_engine, create_session_factory
from market_intelligence.evidence.end_to_end import EndToEndStatus
from market_intelligence.pipeline.multi_provider_ingestion import (
    MultiProviderIngestionPipeline,
    resolve_provider_target,
)
from market_intelligence.providers.contracts import ProviderAdapter, ProviderTransport
from market_intelligence.providers.credentials import RuntimeCredential
from market_intelligence.providers.eia import EiaAdapter
from market_intelligence.providers.finnhub import FinnhubAdapter
from market_intelligence.providers.http_transport import HttpxProviderTransport
from market_intelligence.providers.sec_edgar import SecEdgarAdapter


def dry_run_summary(provider: str, limit: int) -> dict[str, object]:
    return {
        "provider": provider,
        "status": "DRY_RUN",
        "limit": limit,
        "credential_read": False,
        "request_enabled": False,
        "db_written": False,
        "raw_item_count": 0,
        "evidence_item_count": 0,
        "content_item_count": 0,
        "response_saved": False,
        "safe_errors": [],
    }


async def execute_provider(
    provider: str,
    limit: int,
    environ: Mapping[str, str],
    collection_options: Mapping[str, object],
    transport: ProviderTransport | None = None,
) -> tuple[dict[str, object], int]:
    adapter = _adapter(provider, environ)
    if adapter is None:
        return _blocked(provider, limit, "provider_runtime_credential_missing"), 2
    settings = Settings(COLLECTION_BATCH_LIMIT=limit, _env_file=None)  # type: ignore[call-arg]
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        resolved = await resolve_provider_target(factory, provider)
        if resolved is None:
            return _blocked(provider, limit, "provider_target_not_unique", True), 2
        target = CollectionTarget(
            source_id=resolved.target.source_id,
            source_account_id=resolved.target.source_account_id,
            source_type=resolved.target.source_type,
            access_method=resolved.target.access_method,
            retention_class=resolved.target.retention_class,
            collection_options=collection_options,
        )
        pipeline = MultiProviderIngestionPipeline(
            factory, redis, settings, adapter, transport or HttpxProviderTransport()
        )
        outcome = await pipeline.run(target)
        evidence_ids = tuple(
            trigger.pipeline_outcome.evidence_item_id
            for trigger in outcome.trigger_outcomes
            if trigger.pipeline_outcome is not None
            and trigger.pipeline_outcome.evidence_item_id is not None
        )
        content_count = 0
        if outcome.collection_run_id is not None:
            async with factory() as session:
                content_count = int(
                    (
                        await session.scalar(
                            select(func.count())
                            .select_from(ContentItem)
                            .join(RawItem, RawItem.id == ContentItem.raw_item_id)
                            .where(RawItem.collection_run_id == outcome.collection_run_id)
                        )
                    )
                    or 0
                )
        errors = [error.code for error in outcome.safe_errors]
        succeeded = (
            outcome.status is EndToEndStatus.PROCESSED
            and outcome.raw_item_count > 0
            and len(evidence_ids) > 0
            and not errors
        )
        return (
            {
                "provider": provider,
                "status": "PASS" if succeeded else "FAIL",
                "limit": limit,
                "credential_read": True,
                "request_enabled": True,
                "db_written": outcome.raw_item_count > 0 or bool(evidence_ids),
                "raw_item_count": outcome.raw_item_count,
                "evidence_item_count": len(evidence_ids),
                "content_item_count": content_count,
                "response_saved": False,
                "safe_errors": errors,
            },
            0 if succeeded else 3,
        )
    finally:
        await redis.aclose()
        await engine.dispose()


def _adapter(provider: str, environ: Mapping[str, str]) -> ProviderAdapter | None:
    if provider == "finnhub":
        value = environ.get("FINNHUB_API_KEY", "")
        return FinnhubAdapter(RuntimeCredential("FINNHUB_API_KEY", value)) if value else None
    if provider == "eia":
        value = environ.get("EIA_API_KEY", "")
        return EiaAdapter(RuntimeCredential("EIA_API_KEY", value)) if value else None
    if provider == "sec_edgar":
        agent, contact = environ.get("SEC_USER_AGENT", ""), environ.get("SEC_CONTACT_EMAIL", "")
        value = f"{agent} {contact}" if agent and contact else ""
        return SecEdgarAdapter(RuntimeCredential("SEC_USER_AGENT", value)) if value else None
    return None


def _blocked(
    provider: str, limit: int, error: str, credential_read: bool = False
) -> dict[str, object]:
    result = dry_run_summary(provider, limit)
    result.update(status="BLOCKED", credential_read=credential_read, safe_errors=[error])
    return result
