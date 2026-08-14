"""CAS repository and exact worker reload for collection targets."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from market_intelligence.collection.target_configs import OperationContract, OperationRegistry
from market_intelligence.db.models import (
    CollectionTarget,
    CollectionTargetHealthStatus,
    CollectionTargetStatus,
    Source,
    SourceAccount,
)


class TargetRepositoryError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class LoadedTarget:
    target: CollectionTarget
    source: Source
    account: SourceAccount | None
    contract: OperationContract
    config: Mapping[str, Any]


def eligible(source: Source, account: SourceAccount | None, target: CollectionTarget) -> bool:
    if not source.enabled or source.authorization_status.value not in {"authorized", "implemented"}:
        return False
    if target.status is not CollectionTargetStatus.ACTIVE:
        return False
    if target.source_account_id is None:
        return account is None
    return (
        account is not None
        and account.enabled
        and account.identity_status.value == "verified"
        and account.source_id == source.id
    )


class TargetRepository:
    def __init__(
        self, factory: async_sessionmaker[AsyncSession], registry: OperationRegistry
    ) -> None:
        self._factory, self._registry = factory, registry

    async def load_for_execution(self, target_id: UUID, revision: int) -> LoadedTarget:
        async with self._factory() as session:
            target = await session.get(CollectionTarget, target_id)
            if target is None or target.config_revision != revision:
                raise TargetRepositoryError("stale_target_revision")
            source = await session.get(Source, target.source_id)
            account = (
                await session.get(SourceAccount, target.source_account_id)
                if target.source_account_id
                else None
            )
            if source is None or not eligible(source, account, target):
                raise TargetRepositoryError("target_not_eligible")
            contract = self._registry.resolve(
                source.access_method,
                target.operation_key,
                target.operation_config_version,
                target.provider_contract_version,
            )
            config = self._registry.validate(
                contract,
                target.operation_config,
                batch_limit=target.batch_limit,
                max_requests=target.max_requests_per_run,
                max_pages=target.max_pages_per_run,
            )
            return LoadedTarget(target, source, account, contract, config)

    async def revise(
        self, target_id: UUID, expected_revision: int, values: Mapping[str, Any]
    ) -> int:
        forbidden = {
            "id",
            "target_key",
            "source_id",
            "source_account_id",
            "operation_key",
            "legacy_cursor_type",
            "created_at",
        }
        if forbidden & set(values):
            raise TargetRepositoryError("target_identity_immutable")
        safe = dict(values)
        safe["config_revision"] = expected_revision + 1
        safe["health_status"] = CollectionTargetHealthStatus.UNKNOWN
        safe["updated_at"] = datetime.now().astimezone()
        async with self._factory.begin() as session:
            changed = await session.scalar(
                update(CollectionTarget)
                .where(
                    CollectionTarget.id == target_id,
                    CollectionTarget.config_revision == expected_revision,
                )
                .values(**safe)
                .returning(CollectionTarget.config_revision)
            )
            if changed is None:
                raise TargetRepositoryError("target_revision_conflict")
            return int(changed)

    async def due(self, now: datetime, limit: int = 100) -> tuple[UUID, ...]:
        async with self._factory() as session:
            rows = await session.scalars(
                select(CollectionTarget.id)
                .where(
                    CollectionTarget.status == CollectionTargetStatus.ACTIVE,
                    (
                        CollectionTarget.next_retry_at.is_not(None)
                        & (CollectionTarget.next_retry_at <= now)
                    )
                    | (
                        CollectionTarget.next_retry_at.is_(None)
                        & (CollectionTarget.next_due_at <= now)
                    ),
                )
                .order_by(
                    CollectionTarget.next_retry_at.asc().nulls_last(),
                    CollectionTarget.next_due_at,
                    CollectionTarget.priority,
                    CollectionTarget.id,
                )
                .limit(limit)
            )
            return tuple(rows)
