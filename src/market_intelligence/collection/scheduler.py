from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid5

from redis.asyncio import Redis
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from market_intelligence.collection.contracts import CollectionTarget
from market_intelligence.collection.registry import AdapterRegistry
from market_intelligence.db.models import (
    AuthorizationStatus,
    CollectionRun,
    CollectionRunStatus,
    Source,
)

AUTHORIZED_STATUSES = frozenset({AuthorizationStatus.AUTHORIZED, AuthorizationStatus.IMPLEMENTED})


@dataclass(frozen=True, slots=True)
class DispatchRequest:
    target: CollectionTarget
    dispatch_key: str
    task_id: str


def dispatch_key(target: CollectionTarget, scheduled_slot: int) -> str:
    identity = str(target.source_account_id) if target.source_account_id else "source"
    return f"{target.source_id}:{identity}:{scheduled_slot}"


def task_id_for(key: str) -> str:
    return f"collection-{uuid5(NAMESPACE_URL, key)}"


def dispatch_marker_key(task_id: str) -> str:
    return f"collection:dispatch:{task_id}"


def scheduled_slot(now: datetime, schedule_seconds: int) -> int:
    return int(now.timestamp()) // schedule_seconds


def source_is_due(source: Source, latest_run_at: datetime | None, now: datetime) -> bool:
    if source.schedule_seconds is None:
        return False
    anchor = max(
        (value for value in (source.last_success_at, latest_run_at) if value is not None),
        default=None,
    )
    if anchor is None:
        return True
    failure_multiplier = min(2**source.consecutive_failures, 16)
    return now >= anchor + timedelta(seconds=source.schedule_seconds * failure_multiplier)


def eligible_sources_query() -> Select[tuple[Source]]:
    return (
        select(Source)
        .where(
            Source.enabled.is_(True),
            Source.authorization_status.in_(AUTHORIZED_STATUSES),
            Source.schedule_seconds.is_not(None),
            Source.access_method == "fake",
        )
        .options(selectinload(Source.accounts))
        .order_by(Source.id)
        .limit(1000)
    )


async def dispatch_due_targets(
    factory: async_sessionmaker[AsyncSession],
    registry: AdapterRegistry,
    redis: Redis,
    enqueue: Callable[[DispatchRequest], Awaitable[None]],
    *,
    execution_window_seconds: int,
    now: datetime | None = None,
) -> list[DispatchRequest]:
    current = now or datetime.now(UTC)
    requests: list[DispatchRequest] = []
    async with factory() as session:
        sources = list((await session.scalars(eligible_sources_query())).all())
        for source in sources:
            if source.schedule_seconds is None:
                continue
            if not registry.supports(source.access_method):
                continue
            latest_run_at = await session.scalar(
                select(func.max(CollectionRun.started_at)).where(
                    CollectionRun.source_id == source.id
                )
            )
            if not source_is_due(source, latest_run_at, current):
                continue
            enabled_accounts = [account for account in source.accounts if account.enabled]
            account_ids: list[UUID | None] = (
                [account.id for account in enabled_accounts] if source.accounts else [None]
            )
            for account_id in account_ids:
                running = await session.scalar(
                    select(CollectionRun.id).where(
                        CollectionRun.source_id == source.id,
                        CollectionRun.source_account_id == account_id,
                        CollectionRun.status == CollectionRunStatus.RUNNING,
                    )
                )
                if running is not None:
                    continue
                options = (
                    next(
                        account.collection_options
                        for account in enabled_accounts
                        if account.id == account_id
                    )
                    if account_id is not None
                    else {}
                )
                target = CollectionTarget(
                    source_id=source.id,
                    source_account_id=account_id,
                    source_type=source.source_type.value,
                    access_method=source.access_method,
                    retention_class=source.retention_class,
                    collection_options=options,
                )
                key = dispatch_key(target, scheduled_slot(current, source.schedule_seconds))
                request = DispatchRequest(target, key, task_id_for(key))
                marker_key = dispatch_marker_key(request.task_id)
                marker_ttl = source.schedule_seconds + execution_window_seconds
                claimed = await redis.set(
                    marker_key,
                    request.dispatch_key,
                    nx=True,
                    ex=marker_ttl,
                )
                if not claimed:
                    continue
                try:
                    await enqueue(request)
                except Exception:
                    await redis.delete(marker_key)
                    raise
                requests.append(request)
    return requests
