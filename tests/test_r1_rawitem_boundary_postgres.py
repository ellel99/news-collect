from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from market_intelligence.collection.contracts import RawItemEnvelope
from market_intelligence.collection.downstream import persist_fetch_result
from market_intelligence.db.models import (
    AuthorizationStatus,
    CollectionRun,
    CollectionRunStatus,
    ContentItem,
    EventCandidateEvidence,
    EvidenceItem,
    IdentityStatus,
    RawItem,
    Source,
    SourceAccount,
    SourceType,
)
from market_intelligence.providers.contracts import ProviderFetchResult

POSTGRES_TEST_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://market_intelligence:local_dev_only@localhost:5432/market_intelligence",
)


@pytest.mark.asyncio
async def test_r1_fetch_persists_raw_only_without_projection_evidence_or_event() -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    marker = uuid.uuid4().hex
    source_id = account_id = run_id = None
    try:
        async with factory.begin() as session:
            source = Source(
                code=f"r1-raw-only-{marker}",
                name="R1 raw only",
                source_type=SourceType.API,
                access_method="marketaux",
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
                collection_options={"query": "technology"},
            )
            session.add(account)
            await session.flush()
            run = CollectionRun(
                source_id=source.id,
                source_account_id=account.id,
                started_at=datetime.now(UTC),
                status=CollectionRunStatus.RUNNING,
            )
            session.add(run)
            await session.flush()
            source_id, account_id, run_id = source.id, account.id, run.id
            result = ProviderFetchResult(
                raw_items=(
                    RawItemEnvelope(
                        external_id=f"item-{marker}",
                        fetched_at=datetime.now(UTC),
                        http_status=200,
                        content_type="application/json",
                        payload_location=f"internal://r1/{marker}",
                        payload_hash=marker.ljust(64, "0")[:64],
                        retention_class="metadata_only",
                    ),
                ),
                sanitized_metadata=(
                    {
                        "provider_item_id": f"item-{marker}",
                        "published_at": datetime.now(UTC).isoformat(),
                        "field_names": ["title"],
                        "presence_flags": {"title": True},
                    },
                ),
                display_projections=(
                    {
                        "provider_item_id": f"item-{marker}",
                        "published_at": datetime.now(UTC).isoformat(),
                        "display_title": "must not become durable in R1",
                    },
                ),
                next_cursor=None,
                has_more=False,
                safe_errors=(),
                provider="marketaux",
                contract_version=1,
            )
            counts = await persist_fetch_result(
                session,
                run_id=run.id,
                source_id=source.id,
                source_account_id=account.id,
                provider="marketaux",
                result=result,
            )
            assert (counts.fetched, counts.new, counts.duplicates) == (1, 1, 0)

        async with factory() as session:
            assert (
                await session.scalar(
                    select(func.count()).select_from(RawItem).where(RawItem.source_id == source_id)
                )
                == 1
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(ContentItem)
                    .where(ContentItem.source_id == source_id)
                )
                == 0
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(EvidenceItem)
                    .where(EvidenceItem.source_id == source_id)
                )
                == 0
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(EventCandidateEvidence)
                    .join(EvidenceItem, EvidenceItem.id == EventCandidateEvidence.evidence_item_id)
                    .where(EvidenceItem.source_id == source_id)
                )
                == 0
            )
    finally:
        if source_id is not None:
            async with factory.begin() as session:
                await session.execute(delete(RawItem).where(RawItem.source_id == source_id))
                await session.execute(delete(CollectionRun).where(CollectionRun.id == run_id))
                await session.execute(delete(SourceAccount).where(SourceAccount.id == account_id))
                await session.execute(delete(Source).where(Source.id == source_id))
        await engine.dispose()
