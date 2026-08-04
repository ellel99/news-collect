"""Manual Marketaux real collection orchestration over existing services."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from market_intelligence.collection.contracts import CollectionTarget
from market_intelligence.core.config import Settings
from market_intelligence.db.models import AuthorizationStatus, Source, SourceAccount
from market_intelligence.evidence.end_to_end import EndToEndOutcome
from market_intelligence.providers.contracts import ProviderTransport
from market_intelligence.providers.credentials import RuntimeCredential
from market_intelligence.providers.runtime import build_marketaux_real_pipeline


@dataclass(frozen=True, slots=True)
class MarketauxRuntimeTarget:
    target: CollectionTarget


async def resolve_marketaux_target(
    factory: async_sessionmaker[AsyncSession],
) -> MarketauxRuntimeTarget | None:
    """Resolve exactly one enabled, authorized account target or fail closed."""

    async with factory() as session:
        rows = (
            await session.execute(
                select(Source, SourceAccount)
                .join(SourceAccount, SourceAccount.source_id == Source.id)
                .where(
                    Source.access_method == "marketaux",
                    Source.enabled.is_(True),
                    Source.authorization_status.in_(
                        (AuthorizationStatus.AUTHORIZED, AuthorizationStatus.IMPLEMENTED)
                    ),
                    SourceAccount.enabled.is_(True),
                )
                .order_by(Source.code, SourceAccount.id)
                .limit(2)
            )
        ).all()
    if len(rows) != 1:
        return None
    source, account = rows[0]
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
