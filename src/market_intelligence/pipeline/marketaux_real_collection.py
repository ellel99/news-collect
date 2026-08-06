"""Manual Marketaux real collection orchestration over existing services."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from market_intelligence.collection.contracts import CollectionTarget
from market_intelligence.core.config import Settings
from market_intelligence.db.models import (
    AuthorizationStatus,
    IdentityStatus,
    Source,
    SourceAccount,
    SourceType,
)
from market_intelligence.evidence.end_to_end import EndToEndOutcome
from market_intelligence.providers.contracts import ProviderTransport
from market_intelligence.providers.credentials import RuntimeCredential
from market_intelligence.providers.runtime import build_marketaux_real_pipeline


@dataclass(frozen=True, slots=True)
class MarketauxRuntimeTarget:
    target: CollectionTarget


class MarketauxTargetError(StrEnum):
    MISSING = "marketaux_target_missing"
    NOT_UNIQUE = "marketaux_target_not_unique"
    DISABLED = "marketaux_target_disabled"
    UNAUTHORIZED = "marketaux_target_unauthorized"
    ACCOUNT_MISSING = "marketaux_account_missing"


@dataclass(frozen=True, slots=True)
class MarketauxTargetDiagnosis:
    source_count: int
    account_count: int
    eligible_target_count: int
    target: MarketauxRuntimeTarget | None = None
    error: MarketauxTargetError | None = None


@dataclass(frozen=True, slots=True)
class MarketauxTargetBootstrap:
    status: str
    diagnosis: MarketauxTargetDiagnosis


def _runtime_target(source: Source, account: SourceAccount) -> MarketauxRuntimeTarget:
    return MarketauxRuntimeTarget(
        CollectionTarget(
            source_id=source.id,
            source_account_id=account.id,
            source_type=source.source_type.value,
            access_method=source.access_method,
            retention_class=source.retention_class,
            collection_options=account.collection_options,
        )
    )


async def diagnose_marketaux_target(
    factory: async_sessionmaker[AsyncSession],
) -> MarketauxTargetDiagnosis:
    """Describe Marketaux target eligibility without exposing stored configuration."""

    async with factory() as session:
        sources = (
            await session.scalars(
                select(Source)
                .where(Source.access_method == "marketaux")
                .order_by(Source.code, Source.id)
            )
        ).all()
        accounts = (
            await session.scalars(
                select(SourceAccount)
                .join(Source, Source.id == SourceAccount.source_id)
                .where(Source.access_method == "marketaux")
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
        if account.source_id == source.id
    ]

    error: MarketauxTargetError | None = None
    target: MarketauxRuntimeTarget | None = None
    if len(targets) == 1:
        target = _runtime_target(*targets[0])
    elif len(targets) > 1:
        error = MarketauxTargetError.NOT_UNIQUE
    elif not sources:
        error = MarketauxTargetError.MISSING
    elif all(not source.enabled for source in sources):
        error = MarketauxTargetError.DISABLED
    elif not eligible_sources:
        error = MarketauxTargetError.UNAUTHORIZED
    elif not accounts or not any(account.source_id in eligible_source_ids for account in accounts):
        error = MarketauxTargetError.ACCOUNT_MISSING
    elif not any(
        account.enabled and account.source_id in eligible_source_ids for account in accounts
    ):
        error = MarketauxTargetError.DISABLED
    else:
        error = MarketauxTargetError.NOT_UNIQUE

    return MarketauxTargetDiagnosis(
        source_count=len(sources),
        account_count=len(accounts),
        eligible_target_count=len(targets),
        target=target,
        error=error,
    )


async def bootstrap_marketaux_target(
    factory: async_sessionmaker[AsyncSession],
) -> MarketauxTargetBootstrap:
    """Create the minimal local Marketaux target, idempotently and fail closed."""

    diagnosis = await diagnose_marketaux_target(factory)
    if diagnosis.target is not None:
        return MarketauxTargetBootstrap("already_exists", diagnosis)
    if diagnosis.error not in (
        MarketauxTargetError.MISSING,
        MarketauxTargetError.ACCOUNT_MISSING,
    ):
        return MarketauxTargetBootstrap("blocked", diagnosis)

    async with factory.begin() as session:
        sources = (
            await session.scalars(
                select(Source)
                .where(Source.access_method == "marketaux")
                .order_by(Source.code, Source.id)
                .with_for_update()
            )
        ).all()
        if not sources:
            source = Source(
                code="marketaux",
                name="Marketaux",
                source_type=SourceType.API,
                access_method="marketaux",
                authorization_status=AuthorizationStatus.AUTHORIZED,
                retention_class="metadata_only",
                enabled=True,
                schedule_seconds=None,
            )
            session.add(source)
            await session.flush()
        elif len(sources) == 1:
            source = sources[0]
            if not source.enabled:
                return MarketauxTargetBootstrap("blocked", diagnosis)
            if source.authorization_status not in (
                AuthorizationStatus.AUTHORIZED,
                AuthorizationStatus.IMPLEMENTED,
            ):
                return MarketauxTargetBootstrap("blocked", diagnosis)
        else:
            return MarketauxTargetBootstrap("blocked", diagnosis)

        accounts = (
            await session.scalars(
                select(SourceAccount)
                .where(SourceAccount.source_id == source.id)
                .order_by(SourceAccount.id)
                .with_for_update()
            )
        ).all()
        if accounts:
            return MarketauxTargetBootstrap("blocked", diagnosis)
        session.add(
            SourceAccount(
                source_id=source.id,
                identity_status=IdentityStatus.VERIFIED,
                enabled=True,
                collection_options={"query": "technology"},
            )
        )

    refreshed = await diagnose_marketaux_target(factory)
    return MarketauxTargetBootstrap(
        "created" if refreshed.target is not None else "blocked", refreshed
    )


async def resolve_marketaux_target(
    factory: async_sessionmaker[AsyncSession],
) -> MarketauxRuntimeTarget | None:
    """Resolve exactly one enabled, authorized account target or fail closed."""

    return (await diagnose_marketaux_target(factory)).target


class MarketauxRealCollectionPipeline:
    """Execute one explicit Marketaux target through the reviewed pipeline."""

    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        redis: Redis,
        settings: Settings,
        credential: RuntimeCredential,
        transport: ProviderTransport,
    ) -> None:
        self._pipeline = build_marketaux_real_pipeline(
            factory, redis, settings, credential, transport
        )

    async def run(self, target: CollectionTarget) -> EndToEndOutcome:
        return await self._pipeline.run(target)

    async def process_run(self, collection_run_id: UUID) -> EndToEndOutcome:
        return await self._pipeline.process_run(collection_run_id)
