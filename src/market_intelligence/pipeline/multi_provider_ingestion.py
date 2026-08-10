"""Manual bounded ingestion for Finnhub, EIA, and SEC EDGAR."""

from __future__ import annotations

from dataclasses import dataclass

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from market_intelligence.collection.contracts import CollectionTarget
from market_intelligence.collection.registry import build_fake_registry
from market_intelligence.collection.runner import CollectionRunner
from market_intelligence.core.config import Settings
from market_intelligence.db.models import AuthorizationStatus, Source, SourceAccount
from market_intelligence.evidence.end_to_end import (
    EndToEndMockEvidencePipeline,
    EndToEndOutcome,
    EndToEndStatus,
    InMemoryProviderProjectionSidecar,
)
from market_intelligence.feed.provider_feed import ProviderFeedService
from market_intelligence.providers.contracts import ProviderAdapter, ProviderTransport
from market_intelligence.providers.registry import ProviderAdapterRegistry


@dataclass(frozen=True, slots=True)
class ProviderRuntimeTarget:
    target: CollectionTarget


async def resolve_provider_target(
    factory: async_sessionmaker[AsyncSession], provider: str
) -> ProviderRuntimeTarget | None:
    async with factory() as session:
        rows = (
            await session.execute(
                select(Source, SourceAccount)
                .join(SourceAccount, SourceAccount.source_id == Source.id)
                .where(
                    Source.access_method == provider,
                    Source.enabled.is_(True),
                    Source.authorization_status.in_(
                        (AuthorizationStatus.AUTHORIZED, AuthorizationStatus.IMPLEMENTED)
                    ),
                    SourceAccount.enabled.is_(True),
                )
            )
        ).all()
    if len(rows) != 1:
        return None
    source, account = rows[0]
    return ProviderRuntimeTarget(
        CollectionTarget(
            source_id=source.id,
            source_account_id=account.id,
            source_type=source.source_type.value,
            access_method=source.access_method,
            retention_class=source.retention_class,
            collection_options=account.collection_options,
        )
    )


class MultiProviderIngestionPipeline:
    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        redis: Redis,
        settings: Settings,
        adapter: ProviderAdapter,
        transport: ProviderTransport,
    ) -> None:
        self._provider = adapter.provider_key
        self._sidecar = InMemoryProviderProjectionSidecar()
        registry = ProviderAdapterRegistry()
        registry.register(adapter.provider_key, adapter)
        runner = CollectionRunner(
            factory,
            redis,
            build_fake_registry(),
            settings,
            provider_registry=registry,
            provider_transport=transport,
            provider_result_observer=self._sidecar,
        )
        self._pipeline = EndToEndMockEvidencePipeline(factory, runner, self._sidecar)
        self._feed = ProviderFeedService(factory)

    async def run(self, target: CollectionTarget) -> EndToEndOutcome:
        outcome = await self._pipeline.run(target)
        if (
            self._provider == "sec_edgar"
            and outcome.status is EndToEndStatus.PROCESSED
            and outcome.collection_run_id is not None
        ):
            await self._feed.persist_sec_run(outcome.collection_run_id, self._sidecar)
        return outcome
