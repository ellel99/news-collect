"""Explicit provider runtime wiring without environment or scheduler access."""

from __future__ import annotations

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from market_intelligence.collection.registry import build_fake_registry
from market_intelligence.collection.runner import CollectionRunner
from market_intelligence.core.config import Settings
from market_intelligence.evidence.end_to_end import (
    EndToEndMockEvidencePipeline,
    InMemoryProviderProjectionSidecar,
)
from market_intelligence.providers.contracts import ProviderTransport
from market_intelligence.providers.credentials import RuntimeCredential
from market_intelligence.providers.marketaux_real import MarketauxRealAdapter
from market_intelligence.providers.registry import ProviderAdapterRegistry


def build_marketaux_real_pipeline(
    factory: async_sessionmaker[AsyncSession],
    redis: Redis,
    settings: Settings,
    credential: RuntimeCredential,
    transport: ProviderTransport,
    sidecar: InMemoryProviderProjectionSidecar | None = None,
) -> EndToEndMockEvidencePipeline:
    """Compose existing collection/evidence components for explicit manual use."""

    provider_registry = ProviderAdapterRegistry()
    provider_registry.register("marketaux", MarketauxRealAdapter(credential))
    sidecar = sidecar or InMemoryProviderProjectionSidecar()
    runner = CollectionRunner(
        factory,
        redis,
        build_fake_registry(),
        settings,
        provider_registry=provider_registry,
        provider_transport=transport,
        provider_result_observer=sidecar,
    )
    return EndToEndMockEvidencePipeline(factory, runner, sidecar)
