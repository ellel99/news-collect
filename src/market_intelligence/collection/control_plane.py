"""Target-owned scheduler and worker boundary for the R1 control plane."""

from __future__ import annotations

import os
import uuid
from collections.abc import Awaitable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import NAMESPACE_URL, UUID, uuid5

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from market_intelligence.collection.adapter_factory import UnifiedAdapterFactory
from market_intelligence.collection.target_repository import (
    LoadedTarget,
    TargetRepository,
    TargetRepositoryError,
)
from market_intelligence.db.models import (
    CollectionCursor,
    CollectionRun,
    CollectionRunMode,
    CollectionRunStatus,
    CollectionTarget,
    CollectionTargetHealthStatus,
    ParseStatus,
    RawItem,
)
from market_intelligence.providers.contracts import ProviderFetchRequest, ProviderTransport
from market_intelligence.providers.credential_resolver import (
    CredentialResolutionError,
    resolve_runtime_credential,
)


@dataclass(frozen=True, slots=True)
class TargetDispatch:
    target_id: UUID
    config_revision: int
    scheduled_slot: int
    run_mode: str
    dispatch_id: str

    def payload(self) -> dict[str, str | int]:
        return {
            "target_id": str(self.target_id),
            "config_revision": self.config_revision,
            "scheduled_slot": self.scheduled_slot,
            "run_mode": self.run_mode,
            "dispatch_id": self.dispatch_id,
        }


@dataclass(frozen=True, slots=True)
class TargetRunOutcome:
    target_id: UUID
    status: str
    run_id: UUID | None = None
    safe_error: str | None = None


def dispatch_identity(target_id: UUID, revision: int, slot: int, mode: str = "normal") -> str:
    return f"{target_id}:{revision}:{slot}:{mode}"


def dispatch_task_id(identity: str) -> str:
    return f"collection-target-{uuid5(NAMESPACE_URL, identity)}"


class TargetScheduler:
    def __init__(
        self, repository: TargetRepository, redis: Redis, *, marker_ttl: int = 1800
    ) -> None:
        self._repository, self._redis, self._marker_ttl = repository, redis, marker_ttl

    async def claim_due(self, now: datetime, limit: int = 100) -> tuple[TargetDispatch, ...]:
        claimed: list[TargetDispatch] = []
        for target_id in await self._repository.due(now, limit):
            try:
                loaded = await self._repository.load_for_execution(
                    target_id, revision=await self._revision(target_id)
                )
            except TargetRepositoryError:
                continue
            target = loaded.target
            cadence = max(target.cadence_seconds, 1)
            slot = int(now.timestamp()) // cadence
            identity = dispatch_identity(target.id, target.config_revision, slot)
            task_id = dispatch_task_id(identity)
            if await self._redis.set(
                f"r1:dispatch:{task_id}", identity, nx=True, ex=self._marker_ttl
            ):
                claimed.append(
                    TargetDispatch(target.id, target.config_revision, slot, "normal", identity)
                )
        return tuple(claimed)

    async def _revision(self, target_id: UUID) -> int:
        async with self._repository._factory() as session:
            value = await session.scalar(
                select(CollectionTarget.config_revision).where(CollectionTarget.id == target_id)
            )
            if value is None:
                raise TargetRepositoryError("target_not_found")
            return int(value)


class CollectionControlPlaneWorker:
    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        repository: TargetRepository,
        redis: Redis,
        transport: ProviderTransport,
        *,
        adapter_factory: UnifiedAdapterFactory | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self._factory, self._repository, self._redis, self._transport = (
            factory,
            repository,
            redis,
            transport,
        )
        self._adapters = adapter_factory or UnifiedAdapterFactory()
        self._environ = environ if environ is not None else os.environ

    async def execute(self, dispatch: TargetDispatch) -> TargetRunOutcome:
        try:
            loaded = await self._repository.load_for_execution(
                dispatch.target_id, dispatch.config_revision
            )
        except TargetRepositoryError as exc:
            return TargetRunOutcome(dispatch.target_id, "blocked", safe_error=str(exc))
        try:
            credential = resolve_runtime_credential(loaded.source.access_method, self._environ)
        except CredentialResolutionError as exc:
            await self._mark_credential_missing(loaded)
            return TargetRunOutcome(dispatch.target_id, "blocked", safe_error=str(exc))
        owner = uuid.uuid4().hex
        lock_key = f"r1:target-lock:{dispatch.target_id}:{dispatch.run_mode}"
        if not await self._redis.set(
            lock_key, owner, nx=True, ex=loaded.target.max_runtime_seconds + 30
        ):
            return TargetRunOutcome(
                dispatch.target_id, "blocked", safe_error="target_lock_unavailable"
            )
        run_id: UUID | None = None
        try:
            loaded = await self._repository.load_for_execution(
                dispatch.target_id, dispatch.config_revision
            )
            run_id, cursor = await self._start_run(loaded, dispatch)
            adapter = self._adapters.build(
                loaded.source.access_method, loaded.target.operation_key, credential
            )
            request = ProviderFetchRequest(
                source_id=loaded.target.source_id,
                source_account_id=loaded.target.source_account_id,
                cursor=cursor.cursor_value if cursor else None,
                config=loaded.config,
                limit=loaded.target.batch_limit,
                deadline_at=datetime.now(UTC)
                + timedelta(seconds=loaded.target.max_runtime_seconds),
                correlation_id=dispatch.dispatch_id,
                max_response_bytes=loaded.target.max_response_bytes,
            )
            result = await adapter.fetch(request, self._transport)
            if result.safe_errors:
                error = result.safe_errors[0]
                await self._finish_error(
                    loaded, run_id, error.safe_message, error.retryable, error.retry_after_seconds
                )
                return TargetRunOutcome(
                    dispatch.target_id,
                    "retry" if error.retryable else "failed",
                    run_id,
                    error.safe_message,
                )
            await self._persist_result(loaded, dispatch, run_id, cursor, result)
            return TargetRunOutcome(
                dispatch.target_id,
                "partial" if result.has_more else "succeeded",
                run_id,
                "coverage_incomplete" if result.has_more else None,
            )
        except (TargetRepositoryError, ValueError, RuntimeError):
            if run_id is not None:
                await self._finish_error(
                    loaded, run_id, "collection_control_plane_failed", False, None
                )
            return TargetRunOutcome(
                dispatch.target_id, "failed", run_id, "collection_control_plane_failed"
            )
        finally:
            script = (
                "if redis.call('get',KEYS[1])==ARGV[1] then "
                "return redis.call('del',KEYS[1]) else return 0 end"
            )
            await cast(Awaitable[Any], self._redis.eval(script, 1, lock_key, owner))

    async def _start_run(
        self, loaded: LoadedTarget, dispatch: TargetDispatch
    ) -> tuple[UUID, CollectionCursor | None]:
        async with self._factory.begin() as session:
            target = await session.get(CollectionTarget, loaded.target.id, with_for_update=True)
            if target is None or target.config_revision != dispatch.config_revision:
                raise TargetRepositoryError("stale_target_revision")
            run = CollectionRun(
                target_id=target.id,
                run_mode=CollectionRunMode.NORMAL,
                dispatch_identity=dispatch.dispatch_id,
                source_id=target.source_id,
                source_account_id=target.source_account_id,
                started_at=datetime.now(UTC),
                status=CollectionRunStatus.RUNNING,
            )
            session.add(run)
            await session.flush()
            cursor = await session.scalar(
                select(CollectionCursor)
                .where(
                    CollectionCursor.target_id == target.id,
                    CollectionCursor.cursor_type == target.operation_key,
                    CollectionCursor.cursor_version == target.cursor_version,
                    CollectionCursor.run_mode == CollectionRunMode.NORMAL,
                )
                .with_for_update()
            )
            return run.id, cursor

    async def _persist_result(
        self,
        loaded: LoadedTarget,
        dispatch: TargetDispatch,
        run_id: UUID,
        snapshot: CollectionCursor | None,
        result: Any,
    ) -> None:
        async with self._factory.begin() as session:
            target = await session.get(CollectionTarget, loaded.target.id, with_for_update=True)
            run = await session.get(CollectionRun, run_id, with_for_update=True)
            if target is None or run is None or target.config_revision != dispatch.config_revision:
                raise TargetRepositoryError("stale_target_revision")
            for item in result.raw_items:
                session.add(
                    RawItem(
                        source_id=target.source_id,
                        source_account_id=target.source_account_id,
                        collection_run_id=run_id,
                        external_id=item.external_id,
                        fetched_at=item.fetched_at,
                        http_status=item.http_status,
                        content_type=item.content_type,
                        payload_location=item.payload_location,
                        payload_hash=item.payload_hash,
                        retention_class=item.retention_class,
                        parse_status=ParseStatus.PENDING,
                    )
                )
            run.fetched_count = len(result.raw_items)
            run.new_count = len(result.raw_items)
            now = datetime.now(UTC)
            run.finished_at = now
            if result.has_more:
                run.status = CollectionRunStatus.PARTIAL
                run.error_code = "coverage_incomplete"
                run.error_message_redacted = (
                    "provider continuation is unsupported for this operation"
                )
                target.health_status = CollectionTargetHealthStatus.DEGRADED
                target.last_error_code = "coverage_incomplete"
            else:
                run.status = CollectionRunStatus.SUCCEEDED
                target.health_status = CollectionTargetHealthStatus.HEALTHY
                target.last_error_code = None
                target.last_success_at = now
                target.consecutive_failures = 0
            target.last_attempt_at = now
            target.next_retry_at = None
            target.next_due_at = now + timedelta(seconds=target.cadence_seconds)
            if result.next_cursor is not None:
                cursor = await session.scalar(
                    select(CollectionCursor)
                    .where(
                        CollectionCursor.target_id == target.id,
                        CollectionCursor.cursor_type == target.operation_key,
                        CollectionCursor.cursor_version == target.cursor_version,
                        CollectionCursor.run_mode == CollectionRunMode.NORMAL,
                    )
                    .with_for_update()
                )
                if cursor is None:
                    if target.source_account_id is None:
                        raise ValueError("target_cursor_requires_account_during_rollback")
                    cursor = CollectionCursor(
                        source_account_id=target.source_account_id,
                        target_id=target.id,
                        cursor_type=target.operation_key,
                        cursor_version=target.cursor_version,
                        run_mode=CollectionRunMode.NORMAL,
                    )
                    session.add(cursor)
                elif snapshot is not None and cursor.cursor_value != snapshot.cursor_value:
                    raise ValueError("target_cursor_stale")
                cursor.cursor_value = result.next_cursor
                if target.legacy_cursor_type is not None and target.source_account_id is not None:
                    legacy = await session.scalar(
                        select(CollectionCursor)
                        .where(
                            CollectionCursor.source_account_id == target.source_account_id,
                            CollectionCursor.cursor_type == target.legacy_cursor_type,
                        )
                        .with_for_update()
                    )
                    if legacy is None:
                        legacy = CollectionCursor(
                            source_account_id=target.source_account_id,
                            cursor_type=target.legacy_cursor_type,
                        )
                        session.add(legacy)
                    legacy.cursor_value = result.next_cursor

    async def _finish_error(
        self,
        loaded: LoadedTarget,
        run_id: UUID,
        code: str,
        retryable: bool,
        retry_after: float | None,
    ) -> None:
        async with self._factory.begin() as session:
            run = await session.get(CollectionRun, run_id, with_for_update=True)
            target = await session.get(CollectionTarget, loaded.target.id, with_for_update=True)
            if run is None or target is None:
                return
            now = datetime.now(UTC)
            run.status = CollectionRunStatus.RUNNING if retryable else CollectionRunStatus.FAILED
            run.finished_at = None if retryable else now
            run.error_count += 1
            run.error_code = code
            run.error_message_redacted = code
            target.last_attempt_at = now
            target.health_status = CollectionTargetHealthStatus.DEGRADED
            target.last_error_code = code
            target.consecutive_failures += 1
            if retryable:
                delay = min(max(int(retry_after or 30), 1), 900)
                target.next_retry_at = now + timedelta(seconds=delay)
            else:
                target.next_retry_at = None
                target.next_due_at = now + timedelta(seconds=target.cadence_seconds)

    async def _mark_credential_missing(self, loaded: LoadedTarget) -> None:
        async with self._factory.begin() as session:
            target = await session.get(CollectionTarget, loaded.target.id, with_for_update=True)
            if target is not None:
                now = datetime.now(UTC)
                target.health_status = CollectionTargetHealthStatus.DEGRADED
                target.last_error_code = "provider_runtime_credential_missing"
                target.last_attempt_at = now
                target.next_retry_at = None
                target.next_due_at = now + timedelta(seconds=target.cadence_seconds)


async def recover_stale_target_runs(
    factory: async_sessionmaker[AsyncSession],
    redis: Redis,
    *,
    stale_before: datetime,
    retry_delay_seconds: int = 30,
) -> int:
    recovered = 0
    async with factory.begin() as session:
        runs = tuple(
            await session.scalars(
                select(CollectionRun)
                .where(
                    CollectionRun.target_id.is_not(None),
                    CollectionRun.status == CollectionRunStatus.RUNNING,
                    CollectionRun.started_at < stale_before,
                )
                .with_for_update(skip_locked=True)
            )
        )
        for run in runs:
            if run.target_id is None or await redis.exists(
                f"r1:target-lock:{run.target_id}:{run.run_mode.value}"
            ):
                continue
            target = await session.get(CollectionTarget, run.target_id, with_for_update=True)
            if target is None:
                continue
            run.status = CollectionRunStatus.FAILED
            run.finished_at = datetime.now(UTC)
            run.error_code = "stale_run"
            run.error_message_redacted = "stale_run"
            target.health_status = CollectionTargetHealthStatus.DEGRADED
            target.last_error_code = "stale_run"
            target.consecutive_failures += 1
            target.next_retry_at = datetime.now(UTC) + timedelta(seconds=retry_delay_seconds)
            recovered += 1
    return recovered
