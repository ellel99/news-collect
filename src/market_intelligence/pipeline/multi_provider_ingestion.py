"""Manual bounded ingestion for Finnhub, EIA, and SEC EDGAR."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from market_intelligence.collection.contracts import CollectionTarget
from market_intelligence.collection.registry import build_fake_registry
from market_intelligence.collection.runner import CollectionRunner
from market_intelligence.core.config import Settings
from market_intelligence.db.models import (
    AuthorizationStatus,
    IdentityStatus,
    Source,
    SourceAccount,
    SourceType,
)
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


class ProviderTargetError(StrEnum):
    MISSING = "provider_target_missing"
    NOT_UNIQUE = "provider_target_not_unique"
    SOURCE_DISABLED = "provider_source_disabled"
    SOURCE_UNAUTHORIZED = "provider_source_unauthorized"
    ACCOUNT_MISSING = "provider_account_missing"


@dataclass(frozen=True, slots=True)
class ProviderTargetDiagnosis:
    provider: str
    source_count: int
    account_count: int
    eligible_target_count: int
    target: ProviderRuntimeTarget | None = None
    error: ProviderTargetError | None = None


@dataclass(frozen=True, slots=True)
class ProviderTargetBootstrap:
    status: str
    diagnosis: ProviderTargetDiagnosis


_TARGET_DEFAULTS: dict[str, tuple[str, str, dict[str, object]]] = {
    "finnhub": ("Finnhub", "metadata_only", {"symbol": "AAPL"}),
    "eia": ("EIA Open Data", "metadata_only", {"dataset": "electricity"}),
    "sec_edgar": (
        "SEC EDGAR",
        "link_only",
        {"ticker": "AAPL", "cik": "0000320193"},
    ),
}


def _runtime_target(source: Source, account: SourceAccount) -> ProviderRuntimeTarget:
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


async def diagnose_provider_target(
    factory: async_sessionmaker[AsyncSession], provider: str
) -> ProviderTargetDiagnosis:
    if provider not in _TARGET_DEFAULTS:
        raise ValueError("provider_unsupported")
    async with factory() as session:
        sources = (
            await session.scalars(
                select(Source)
                .where(Source.access_method == provider)
                .order_by(Source.code, Source.id)
            )
        ).all()
        accounts = (
            await session.scalars(
                select(SourceAccount)
                .join(Source, Source.id == SourceAccount.source_id)
                .where(Source.access_method == provider)
                .order_by(SourceAccount.id)
            )
        ).all()
    eligible_sources = [
        source
        for source in sources
        if source.enabled
        and source.authorization_status
        in (AuthorizationStatus.AUTHORIZED, AuthorizationStatus.IMPLEMENTED)
    ]
    eligible_source_ids = {source.id for source in eligible_sources}
    eligible_accounts = [
        account
        for account in accounts
        if account.enabled and account.source_id in eligible_source_ids
    ]
    targets = [
        (source, account)
        for source in eligible_sources
        for account in eligible_accounts
        if source.id == account.source_id
    ]
    target = _runtime_target(*targets[0]) if len(targets) == 1 else None
    error: ProviderTargetError | None = None
    if len(targets) > 1:
        error = ProviderTargetError.NOT_UNIQUE
    elif target is None and not sources:
        error = ProviderTargetError.MISSING
    elif target is None and all(not source.enabled for source in sources):
        error = ProviderTargetError.SOURCE_DISABLED
    elif target is None and not eligible_sources:
        error = ProviderTargetError.SOURCE_UNAUTHORIZED
    elif target is None and not eligible_accounts:
        error = ProviderTargetError.ACCOUNT_MISSING
    elif target is None:
        error = ProviderTargetError.NOT_UNIQUE
    return ProviderTargetDiagnosis(
        provider=provider,
        source_count=len(sources),
        account_count=len(accounts),
        eligible_target_count=len(targets),
        target=target,
        error=error,
    )


async def bootstrap_provider_target(
    factory: async_sessionmaker[AsyncSession], provider: str
) -> ProviderTargetBootstrap:
    """Create only a missing minimal target; never repair ambiguous rows."""

    diagnosis = await diagnose_provider_target(factory, provider)
    if diagnosis.target is not None:
        return ProviderTargetBootstrap("already_exists", diagnosis)
    if diagnosis.error not in (
        ProviderTargetError.MISSING,
        ProviderTargetError.ACCOUNT_MISSING,
    ):
        return ProviderTargetBootstrap("blocked", diagnosis)
    name, retention_class, options = _TARGET_DEFAULTS[provider]
    async with factory.begin() as session:
        sources = (
            await session.scalars(
                select(Source)
                .where(Source.access_method == provider)
                .order_by(Source.code, Source.id)
                .with_for_update()
            )
        ).all()
        if not sources:
            source = Source(
                code=provider.replace("_", "-"),
                name=name,
                source_type=SourceType.API,
                access_method=provider,
                authorization_status=AuthorizationStatus.AUTHORIZED,
                retention_class=retention_class,
                enabled=True,
                schedule_seconds=None,
            )
            session.add(source)
            await session.flush()
        elif len(sources) == 1:
            source = sources[0]
            if not source.enabled or source.authorization_status not in (
                AuthorizationStatus.AUTHORIZED,
                AuthorizationStatus.IMPLEMENTED,
            ):
                return ProviderTargetBootstrap("blocked", diagnosis)
        else:
            return ProviderTargetBootstrap("blocked", diagnosis)
        accounts = (
            await session.scalars(
                select(SourceAccount)
                .where(SourceAccount.source_id == source.id)
                .order_by(SourceAccount.id)
                .with_for_update()
            )
        ).all()
        if accounts:
            return ProviderTargetBootstrap("blocked", diagnosis)
        session.add(
            SourceAccount(
                source_id=source.id,
                identity_status=IdentityStatus.VERIFIED,
                enabled=True,
                collection_options=options,
            )
        )
    refreshed = await diagnose_provider_target(factory, provider)
    return ProviderTargetBootstrap(
        "created" if refreshed.target is not None else "blocked", refreshed
    )


async def resolve_provider_target(
    factory: async_sessionmaker[AsyncSession], provider: str
) -> ProviderRuntimeTarget | None:
    return (await diagnose_provider_target(factory, provider)).target


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
