from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from market_intelligence.collection.contracts import RawItemEnvelope
from market_intelligence.collection.downstream import persist_fetch_result
from market_intelligence.db.models import (
    AuthorizationStatus,
    CollectionBackfillPolicy,
    CollectionCursorStrategy,
    CollectionMode,
    CollectionRevisionPolicy,
    CollectionRun,
    CollectionRunMode,
    CollectionRunStatus,
    CollectionTarget,
    CollectionTargetHealthStatus,
    CollectionTargetStatus,
    ContentItem,
    EvidenceItem,
    IdentityStatus,
    Notification,
    RawItem,
    Source,
    SourceAccount,
    SourceType,
)
from market_intelligence.notifications.intent import (
    IntentWatermark,
    persist_cutover_watermark,
)
from market_intelligence.providers.contracts import ProviderFetchResult

POSTGRES_TEST_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://market_intelligence:local_dev_only@localhost:5432/market_intelligence",
)
ZERO_UUID = UUID(int=0)


def _projection(provider: str) -> dict[str, object]:
    common: dict[str, object] = {
        "provider_item_id": f"{provider}-item-1",
        "published_at": "2026-08-01T00:00:00+00:00",
        "field_names": ("safe_field",),
    }
    if provider == "marketaux":
        common.update(
            has_title=True,
            has_description=True,
            has_snippet=True,
            has_source_url=True,
        )
    elif provider == "finnhub":
        common.update(symbol="AAPL", numeric_field_count=3)
    elif provider == "eia":
        common.update(geography="US", sector="ALL", has_numeric_value=True)
    else:
        common.update(ticker="AAPL", form="10-Q", has_primary_document=True)
    return common


def _display(provider: str) -> dict[str, object]:
    return {
        "provider_item_id": f"{provider}-item-1",
        "published_at": "2026-08-01T00:00:00+00:00",
        "display_title": f"Safe {provider} observation",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ("marketaux", "finnhub", "eia", "sec_edgar"))
async def test_provider_fetch_result_persists_complete_idempotent_chain(provider: str) -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    async with engine.connect() as connection:
        outer = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            source = Source(
                code=f"r1-pipeline-{provider}-{ZERO_UUID.hex[:8]}",
                name=f"R1 {provider}",
                source_type=SourceType.API,
                access_method=provider,
                authorization_status=AuthorizationStatus.AUTHORIZED,
                retention_class="metadata_only",
                enabled=True,
            )
            session.add(source)
            await session.flush()
            account = SourceAccount(
                source_id=source.id,
                identity_status=IdentityStatus.VERIFIED,
                enabled=True,
                collection_options={},
            )
            session.add(account)
            await session.flush()
            target = CollectionTarget(
                target_key=f"r1.pipeline.{provider}.{account.id}",
                source_id=source.id,
                source_account_id=account.id,
                operation_key={
                    "marketaux": "news_all",
                    "finnhub": "quote",
                    "eia": "electricity_retail_sales",
                    "sec_edgar": "submissions_recent",
                }[provider],
                legacy_cursor_type="provider_cursor_v1",
                operation_config_version=1,
                provider_contract_version=1,
                operation_config={},
                status=CollectionTargetStatus.ACTIVE,
                cadence_seconds=300,
                batch_limit=1,
                max_requests_per_run=1,
                max_pages_per_run=1,
                max_response_bytes=1_000_000,
                request_timeout_seconds=10,
                max_runtime_seconds=60,
                cursor_strategy=(
                    CollectionCursorStrategy.REVISION
                    if provider == "sec_edgar"
                    else CollectionCursorStrategy.COMPOUND
                ),
                cursor_version=1,
                collection_mode=(
                    CollectionMode.SNAPSHOT
                    if provider in {"eia", "sec_edgar"}
                    else CollectionMode.INCREMENTAL
                ),
                backfill_policy=CollectionBackfillPolicy.DISABLED,
                revision_policy=CollectionRevisionPolicy.IGNORE,
                rate_limit_group=f"{provider}:test",
                next_due_at=datetime.now(UTC),
                health_status=CollectionTargetHealthStatus.UNKNOWN,
            )
            session.add(target)
            await session.flush()
            run = CollectionRun(
                target_id=target.id,
                run_mode=CollectionRunMode.NORMAL,
                dispatch_identity=f"test:{provider}",
                source_id=source.id,
                source_account_id=account.id,
                started_at=datetime.now(UTC),
                status=CollectionRunStatus.RUNNING,
            )
            session.add(run)
            await session.flush()
            await persist_cutover_watermark(
                session, IntentWatermark(datetime(2000, 1, 1, tzinfo=UTC), ZERO_UUID)
            )
            projection = _projection(provider)
            payload_hash = (provider.encode().hex() + "0" * 64)[:64]
            envelope = RawItemEnvelope(
                external_id=f"{provider}-item-1",
                fetched_at=datetime.now(UTC),
                http_status=200,
                content_type="application/json",
                payload_location=f"internal://provider/{provider}/{payload_hash}",
                payload_hash=payload_hash,
                retention_class="metadata_only",
            )
            result = ProviderFetchResult(
                raw_items=(envelope,),
                sanitized_metadata=(projection,),
                display_projections=(_display(provider),),
                next_cursor=None,
                has_more=False,
                safe_errors=(),
                provider=provider,
                contract_version=1,
            )

            first = await persist_fetch_result(
                session,
                run_id=run.id,
                source_id=source.id,
                source_account_id=account.id,
                provider=provider,
                result=result,
            )
            second = await persist_fetch_result(
                session,
                run_id=run.id,
                source_id=source.id,
                source_account_id=account.id,
                provider=provider,
                result=result,
            )
            await session.flush()
            assert (first.fetched, first.new, first.duplicates) == (1, 1, 0)
            assert (second.fetched, second.new, second.duplicates) == (1, 0, 1)
            assert (
                await session.scalar(
                    select(func.count()).select_from(RawItem).where(RawItem.source_id == source.id)
                )
                == 1
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(ContentItem)
                    .where(ContentItem.source_id == source.id)
                )
                == 1
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(EvidenceItem)
                    .where(EvidenceItem.source_id == source.id)
                )
                == 1
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(Notification)
                    .join(ContentItem, ContentItem.id == Notification.content_item_id)
                    .where(ContentItem.source_id == source.id)
                )
                == 1
            )
            content = await session.scalar(
                select(ContentItem).where(ContentItem.source_id == source.id)
            )
            evidence = await session.scalar(
                select(EvidenceItem).where(EvidenceItem.source_id == source.id)
            )
            assert content is not None and evidence is not None
            assert content.raw_item_id == evidence.raw_item_id
            assert evidence.content_item_id == content.id
            assert content.body is None
            assert "raw response" not in repr((content.metadata_, evidence.errors)).lower()
        finally:
            await session.close()
            await outer.rollback()
    await engine.dispose()
