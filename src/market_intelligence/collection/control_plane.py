"""Target-owned scheduler and worker boundary for the R1 control plane."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import uuid
from collections.abc import Awaitable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import NAMESPACE_URL, UUID, uuid5

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from market_intelligence.collection.adapter_factory import UnifiedAdapterFactory
from market_intelligence.collection.cursors import CursorContractError, decide_cursor
from market_intelligence.collection.downstream import (
    persist_fetch_result,
)
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


class TargetLockLost(RuntimeError):
    pass


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
        if not 1 <= limit <= 500:
            raise ValueError("dispatch_budget_invalid")
        claimed: list[TargetDispatch] = []
        after: tuple[datetime, int, UUID] | None = None
        while len(claimed) < limit:
            page = await self._repository.due_page(now, min(100, limit), after)
            if not page:
                break
            for due in page:
                after = (due.effective_due_at, due.priority, due.target_id)
                try:
                    loaded = await self._repository.load_for_execution(
                        due.target_id, revision=await self._revision(due.target_id)
                    )
                except TargetRepositoryError:
                    continue
                target = loaded.target
                cadence = max(target.cadence_seconds, 1)
                slot = (
                    int(due.effective_due_at.timestamp())
                    if due.retry_due
                    else int(now.timestamp()) // cadence
                )
                identity = dispatch_identity(target.id, target.config_revision, slot)
                task_id = dispatch_task_id(identity)
                ttl = max(
                    self._marker_ttl,
                    cadence,
                    target.max_runtime_seconds + 900,
                )
                if await self._redis.set(f"r1:dispatch:{task_id}", identity, nx=True, ex=ttl):
                    claimed.append(
                        TargetDispatch(target.id, target.config_revision, slot, "normal", identity)
                    )
                    if len(claimed) >= limit:
                        break
            if len(page) < min(100, limit):
                break
        return tuple(claimed)

    async def release(self, dispatch: TargetDispatch) -> None:
        key = f"r1:dispatch:{dispatch_task_id(dispatch.dispatch_id)}"
        script = (
            "if redis.call('get',KEYS[1])==ARGV[1] then "
            "return redis.call('del',KEYS[1]) else return 0 end"
        )
        await cast(Awaitable[Any], self._redis.eval(script, 1, key, dispatch.dispatch_id))

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
            run_mode = CollectionRunMode(dispatch.run_mode)
        except ValueError:
            return TargetRunOutcome(dispatch.target_id, "blocked", safe_error="run_mode_invalid")
        expected_dispatch = dispatch_identity(
            dispatch.target_id,
            dispatch.config_revision,
            dispatch.scheduled_slot,
            run_mode.value,
        )
        if dispatch.dispatch_id != expected_dispatch:
            return TargetRunOutcome(
                dispatch.target_id, "blocked", safe_error="dispatch_identity_invalid"
            )
        try:
            loaded = await self._repository.load_for_execution(
                dispatch.target_id, dispatch.config_revision
            )
        except TargetRepositoryError as exc:
            return TargetRunOutcome(dispatch.target_id, "blocked", safe_error=str(exc))
        if await self._redis.exists(f"r1:rate-limit:{loaded.target.rate_limit_group}"):
            return TargetRunOutcome(
                dispatch.target_id, "retry", safe_error="rate_limit_group_cooldown"
            )
        owner = uuid.uuid4().hex
        lock_key = f"r1:target-lock:{dispatch.target_id}:{dispatch.run_mode}"
        if not await self._redis.set(
            lock_key, owner, nx=True, ex=loaded.target.max_runtime_seconds + 30
        ):
            return TargetRunOutcome(
                dispatch.target_id, "blocked", safe_error="target_lock_unavailable"
            )
        run_id: UUID | None = None
        stop_renewal = asyncio.Event()
        lock_lost = asyncio.Event()
        renewal = asyncio.create_task(
            self._renew_lock(
                lock_key,
                owner,
                loaded.target.max_runtime_seconds + 30,
                stop_renewal,
                lock_lost,
            )
        )
        try:
            loaded = await self._repository.load_for_execution(
                dispatch.target_id, dispatch.config_revision
            )
            try:
                credential = resolve_runtime_credential(loaded.source.access_method, self._environ)
            except CredentialResolutionError as exc:
                await self._mark_credential_missing(loaded)
                return TargetRunOutcome(dispatch.target_id, "blocked", safe_error=str(exc))
            loaded = await self._repository.load_for_execution(
                dispatch.target_id, dispatch.config_revision
            )
            run_id, cursor = await self._start_run(loaded, dispatch, run_mode)
            adapter = self._adapters.build(
                loaded.source.access_method,
                loaded.target.operation_key,
                credential,
                *(
                    [loaded.target.provider_contract_version]
                    if loaded.target.provider_contract_version != 1
                    else []
                ),
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
                request_timeout_seconds=loaded.target.request_timeout_seconds,
                continuation=cursor.continuation if cursor else None,
            )
            if loaded.contract.pagination_capability == "bounded_window_v1":
                return await self._execute_pages(
                    loaded,
                    dispatch,
                    run_id,
                    cursor,
                    request,
                    adapter,
                    lock_lost,
                    lock_key,
                    owner,
                    run_mode,
                )
            try:
                result = await self._fetch_with_lock(
                    adapter.fetch(request, self._transport),
                    lock_lost,
                    loaded.target.max_runtime_seconds,
                )
            except TargetLockLost:
                await self._finish_lock_lost(loaded, dispatch, run_id)
                return TargetRunOutcome(dispatch.target_id, "retry", run_id, "target_lock_lost")
            except TimeoutError:
                will_retry = await self._finish_error(
                    loaded, dispatch, run_id, "provider_runtime_deadline_exceeded", True, None
                )
                return TargetRunOutcome(
                    dispatch.target_id,
                    "retry" if will_retry else "failed",
                    run_id,
                    "provider_runtime_deadline_exceeded",
                )
            if result.safe_errors:
                error = result.safe_errors[0]
                if error.code.value == "provider_rate_limited":
                    delay = min(max(int(error.retry_after_seconds or 30), 1), 900)
                    await self._redis.set(
                        f"r1:rate-limit:{loaded.target.rate_limit_group}",
                        "cooldown",
                        ex=delay,
                    )
                will_retry = await self._finish_error(
                    loaded,
                    dispatch,
                    run_id,
                    error.safe_message,
                    error.retryable,
                    error.retry_after_seconds,
                )
                return TargetRunOutcome(
                    dispatch.target_id,
                    "retry" if will_retry else "failed",
                    run_id,
                    error.safe_message,
                )
            lock_owner = await self._redis.get(lock_key)
            if lock_owner not in (owner, owner.encode()):
                await self._finish_lock_lost(loaded, dispatch, run_id)
                return TargetRunOutcome(dispatch.target_id, "retry", run_id, "target_lock_lost")
            try:
                cursor_decision = decide_cursor(
                    loaded.target.cursor_strategy,
                    cursor.cursor_value if cursor else None,
                    result.next_cursor,
                )
            except CursorContractError:
                await self._finish_error(
                    loaded, dispatch, run_id, "cursor_contract_invalid", False, None
                )
                return TargetRunOutcome(
                    dispatch.target_id, "failed", run_id, "cursor_contract_invalid"
                )
            if cursor_decision.action == "no_new_items":
                result = replace(
                    result,
                    raw_items=(),
                    sanitized_metadata=(),
                    display_projections=(),
                    next_cursor=None,
                    has_more=False,
                )
            await self._persist_result(loaded, dispatch, run_id, cursor, result, run_mode)
            return TargetRunOutcome(
                dispatch.target_id,
                "partial"
                if result.has_more
                else "no_new_items"
                if cursor_decision.action == "no_new_items"
                else "succeeded",
                run_id,
                "coverage_incomplete" if result.has_more else None,
            )
        except TargetLockLost:
            if run_id is not None:
                await self._finish_lock_lost(loaded, dispatch, run_id)
            return TargetRunOutcome(dispatch.target_id, "retry", run_id, "target_lock_lost")
        except SQLAlchemyError:
            if run_id is not None:
                retry = await self._finish_error(
                    loaded, dispatch, run_id, "collection_database_failed", True, None
                )
                return TargetRunOutcome(
                    dispatch.target_id,
                    "retry" if retry else "failed",
                    run_id,
                    "collection_database_failed",
                )
            return TargetRunOutcome(
                dispatch.target_id, "failed", safe_error="collection_database_failed"
            )
        except (TargetRepositoryError, ValueError, RuntimeError):
            if run_id is not None:
                await self._finish_error(
                    loaded,
                    dispatch,
                    run_id,
                    "collection_control_plane_failed",
                    False,
                    None,
                )
            return TargetRunOutcome(
                dispatch.target_id, "failed", run_id, "collection_control_plane_failed"
            )
        finally:
            stop_renewal.set()
            await renewal
            script = (
                "if redis.call('get',KEYS[1])==ARGV[1] then "
                "return redis.call('del',KEYS[1]) else return 0 end"
            )
            await cast(Awaitable[Any], self._redis.eval(script, 1, lock_key, owner))

    async def _start_run(
        self,
        loaded: LoadedTarget,
        dispatch: TargetDispatch,
        run_mode: CollectionRunMode,
    ) -> tuple[UUID, CollectionCursor | None]:
        async with self._factory.begin() as session:
            target = await session.get(CollectionTarget, loaded.target.id, with_for_update=True)
            if target is None or target.config_revision != dispatch.config_revision:
                raise TargetRepositoryError("stale_target_revision")
            existing = await session.scalar(
                select(CollectionRun)
                .where(
                    CollectionRun.target_id == target.id,
                    CollectionRun.run_mode == run_mode,
                    CollectionRun.status == CollectionRunStatus.RUNNING,
                )
                .with_for_update()
            )
            if existing is not None:
                cursor = await session.scalar(
                    select(CollectionCursor)
                    .where(
                        CollectionCursor.target_id == target.id,
                        CollectionCursor.cursor_type == target.operation_key,
                        CollectionCursor.cursor_version == target.cursor_version,
                        CollectionCursor.run_mode == run_mode,
                    )
                    .with_for_update()
                )
                return existing.id, cursor
            run = CollectionRun(
                target_id=target.id,
                run_mode=run_mode,
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
                    CollectionCursor.run_mode == run_mode,
                )
                .with_for_update()
            )
            return run.id, cursor

    async def _execute_pages(
        self,
        loaded: LoadedTarget,
        dispatch: TargetDispatch,
        run_id: UUID,
        cursor: CollectionCursor | None,
        request: ProviderFetchRequest,
        adapter: Any,
        lock_lost: asyncio.Event,
        lock_key: str,
        owner: str,
        run_mode: CollectionRunMode,
    ) -> TargetRunOutcome:
        pages = min(loaded.target.max_pages_per_run, loaded.target.max_requests_per_run)
        for index in range(pages):
            await self._repository.load_for_execution(dispatch.target_id, dispatch.config_revision)
            async with self._factory.begin() as session:
                run = await session.get(CollectionRun, run_id, with_for_update=True)
                assert run is not None
                if (
                    run.request_count >= loaded.target.max_requests_per_run
                    or run.page_count >= loaded.target.max_pages_per_run
                ):
                    run.status = CollectionRunStatus.PARTIAL
                    run.finished_at = datetime.now(UTC)
                    run.error_code = "coverage_incomplete"
                    target = await session.get(
                        CollectionTarget, dispatch.target_id, with_for_update=True
                    )
                    assert target is not None
                    target.next_retry_at = None
                    target.next_due_at = datetime.now(UTC) + timedelta(
                        seconds=target.cadence_seconds
                    )
                    target.health_status = CollectionTargetHealthStatus.DEGRADED
                    target.last_error_code = "coverage_incomplete"
                    return TargetRunOutcome(
                        dispatch.target_id, "partial", run_id, "coverage_incomplete"
                    )
                run.request_count += 1
                run_deadline = run.started_at + timedelta(seconds=loaded.target.max_runtime_seconds)
            remaining = (min(request.deadline_at, run_deadline) - datetime.now(UTC)).total_seconds()
            if remaining <= 0:
                await self._finish_error(
                    loaded, dispatch, run_id, "provider_runtime_deadline_exceeded", False, None
                )
                return TargetRunOutcome(
                    dispatch.target_id, "failed", run_id, "provider_runtime_deadline_exceeded"
                )
            try:
                result = await self._fetch_with_lock(
                    adapter.fetch(request, self._transport), lock_lost, max(1, int(remaining))
                )
            except TargetLockLost:
                await self._finish_lock_lost(loaded, dispatch, run_id)
                return TargetRunOutcome(dispatch.target_id, "retry", run_id, "target_lock_lost")
            except TimeoutError:
                await self._finish_error(
                    loaded, dispatch, run_id, "provider_runtime_deadline_exceeded", False, None
                )
                return TargetRunOutcome(
                    dispatch.target_id, "failed", run_id, "provider_runtime_deadline_exceeded"
                )
            if result.safe_errors:
                error = result.safe_errors[0]
                if error.code.value == "provider_rate_limited":
                    await self._redis.set(
                        f"r1:rate-limit:{loaded.target.rate_limit_group}",
                        "cooldown",
                        ex=min(max(int(error.retry_after_seconds or 30), 1), 900),
                    )
                retry = await self._finish_error(
                    loaded,
                    dispatch,
                    run_id,
                    error.safe_message,
                    error.retryable,
                    error.retry_after_seconds,
                )
                return TargetRunOutcome(
                    dispatch.target_id, "retry" if retry else "failed", run_id, error.safe_message
                )
            if (
                result.contract_version != loaded.target.provider_contract_version
                or result.provider != loaded.source.access_method
            ):
                raise ValueError("provider_contract_mismatch")
            if await self._redis.get(lock_key) not in (owner, owner.encode()):
                raise TargetLockLost("target_lock_lost")
            if result.has_more and (
                not result.continuation or result.continuation == request.continuation
            ):
                raise ValueError("provider_continuation_invalid")
            final = not result.has_more or index + 1 == pages
            await self._persist_result(
                loaded,
                dispatch,
                run_id,
                cursor,
                result,
                run_mode,
                page_mode=True,
                final=final,
                observation_key=hashlib.sha256(
                    json.dumps(dict(request.continuation or {}), sort_keys=True).encode()
                ).hexdigest(),
            )
            if final:
                return TargetRunOutcome(
                    dispatch.target_id,
                    "partial" if result.has_more else "succeeded",
                    run_id,
                    "coverage_incomplete" if result.has_more else None,
                )
            async with self._factory() as session:
                cursor = await session.scalar(
                    select(CollectionCursor).where(
                        CollectionCursor.target_id == dispatch.target_id,
                        CollectionCursor.cursor_type == loaded.target.operation_key,
                        CollectionCursor.run_mode == run_mode,
                    )
                )
            request = replace(request, cursor=result.next_cursor, continuation=result.continuation)
        raise ValueError("operation_budget_invalid")

    async def _persist_result(
        self,
        loaded: LoadedTarget,
        dispatch: TargetDispatch,
        run_id: UUID,
        snapshot: CollectionCursor | None,
        result: Any,
        run_mode: CollectionRunMode,
        *,
        page_mode: bool = False,
        final: bool = True,
        observation_key: str = "run",
    ) -> None:
        async with self._factory.begin() as session:
            target = await session.get(CollectionTarget, loaded.target.id, with_for_update=True)
            run = await session.get(CollectionRun, run_id, with_for_update=True)
            if target is None or run is None or target.config_revision != dispatch.config_revision:
                raise TargetRepositoryError("stale_target_revision")
            counts = await persist_fetch_result(
                session,
                run_id=run_id,
                source_id=target.source_id,
                source_account_id=target.source_account_id,
                provider=loaded.source.access_method,
                target_id=target.id,
                operation_key=target.operation_key,
                config_revision=target.config_revision,
                provider_contract_version=target.provider_contract_version,
                result=result,
                observation_key=observation_key,
            )
            run.fetched_count = (run.fetched_count if page_mode else 0) + counts.fetched
            run.new_count = (run.new_count if page_mode else 0) + counts.new
            run.duplicate_count = (run.duplicate_count if page_mode else 0) + counts.duplicates
            if page_mode:
                run.page_count += 1
            now = datetime.now(UTC)
            run.finished_at = now if final else None
            if not final:
                run.status = CollectionRunStatus.RUNNING
            elif result.has_more:
                run.status = CollectionRunStatus.PARTIAL
                run.error_code = "coverage_incomplete"
                run.error_message_redacted = (
                    "bounded run budget exhausted"
                    if page_mode
                    else "provider continuation is unsupported for this operation"
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
            if result.next_cursor is not None or result.has_more:
                cursor = await session.scalar(
                    select(CollectionCursor)
                    .where(
                        CollectionCursor.target_id == target.id,
                        CollectionCursor.cursor_type == target.operation_key,
                        CollectionCursor.cursor_version == target.cursor_version,
                        CollectionCursor.run_mode == run_mode,
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
                        run_mode=run_mode,
                    )
                    session.add(cursor)
                elif snapshot is not None and cursor.cursor_value != snapshot.cursor_value:
                    raise ValueError("target_cursor_stale")
                if page_mode:
                    position = decide_cursor(
                        target.cursor_strategy, cursor.cursor_value, result.next_cursor
                    ).candidate
                    cursor.cursor_value = result.next_cursor
                    cursor.continuation = dict(result.continuation) if result.continuation else None
                    if position is not None:
                        cursor.last_published_at = position.published_at
                        cursor.watermark_at = position.published_at
                elif result.has_more:
                    cursor.continuation = {
                        "coverage_incomplete": True,
                        "continuation_unsupported": True,
                    }
                elif result.next_cursor is not None:
                    position = decide_cursor(
                        target.cursor_strategy, cursor.cursor_value, result.next_cursor
                    ).candidate
                    cursor.cursor_value = result.next_cursor
                    cursor.continuation = None
                    if position is not None:
                        cursor.last_published_at = position.published_at
                        cursor.watermark_at = position.published_at
                if (
                    not result.has_more
                    and result.next_cursor is not None
                    and target.legacy_cursor_type is not None
                    and target.source_account_id is not None
                ):
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
        dispatch: TargetDispatch,
        run_id: UUID,
        code: str,
        retryable: bool,
        retry_after: float | None,
    ) -> bool:
        async with self._factory.begin() as session:
            run = await session.get(CollectionRun, run_id, with_for_update=True)
            target = await session.get(CollectionTarget, loaded.target.id, with_for_update=True)
            if run is None or target is None:
                return False
            if target.config_revision != dispatch.config_revision:
                run.status = CollectionRunStatus.FAILED
                run.finished_at = datetime.now(UTC)
                run.error_code = "stale_target_revision"
                run.error_message_redacted = "stale_target_revision"
                return False
            now = datetime.now(UTC)
            retryable = retryable and run.retry_count < 3
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
                run.retry_count += 1
                delay = min(max(int(retry_after or 30), 1), 900)
                target.next_retry_at = now + timedelta(seconds=delay)
            else:
                target.next_retry_at = None
                target.next_due_at = now + timedelta(seconds=target.cadence_seconds)
            return retryable

    async def _finish_lock_lost(
        self,
        loaded: LoadedTarget,
        dispatch: TargetDispatch,
        run_id: UUID,
    ) -> None:
        """Terminate the old lineage and schedule a distinct run after lock loss."""

        async with self._factory.begin() as session:
            run = await session.get(CollectionRun, run_id, with_for_update=True)
            target = await session.get(CollectionTarget, loaded.target.id, with_for_update=True)
            if run is None or target is None:
                return
            now = datetime.now(UTC)
            run.status = CollectionRunStatus.FAILED
            run.finished_at = now
            run.error_count += 1
            run.error_code = "target_lock_lost"
            run.error_message_redacted = "target_lock_lost"
            if target.config_revision != dispatch.config_revision:
                return
            target.last_attempt_at = now
            target.health_status = CollectionTargetHealthStatus.DEGRADED
            target.last_error_code = "target_lock_lost"
            target.consecutive_failures += 1
            target.next_retry_at = now + timedelta(seconds=30)

    async def _renew_lock(
        self,
        key: str,
        owner: str,
        ttl: int,
        stop: asyncio.Event,
        lock_lost: asyncio.Event,
    ) -> None:
        interval = max(min(ttl // 3, 30), 1)
        script = (
            "if redis.call('get',KEYS[1])==ARGV[1] then "
            "return redis.call('expire',KEYS[1],ARGV[2]) else return 0 end"
        )
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except TimeoutError:
                renewed = await cast(
                    Awaitable[Any], self._redis.eval(script, 1, key, owner, str(ttl))
                )
                if not renewed:
                    lock_lost.set()
                    return

    @staticmethod
    async def _fetch_with_lock(
        fetch: Awaitable[Any],
        lock_lost: asyncio.Event,
        timeout_seconds: int,
    ) -> Any:
        fetch_task: asyncio.Future[Any] = asyncio.ensure_future(fetch)
        lost_task = asyncio.create_task(lock_lost.wait())
        try:
            async with asyncio.timeout(timeout_seconds):
                done, _ = await asyncio.wait(
                    {fetch_task, lost_task}, return_when=asyncio.FIRST_COMPLETED
                )
                if lost_task in done and lock_lost.is_set():
                    fetch_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await fetch_task
                    raise TargetLockLost("target_lock_lost")
                return await fetch_task
        finally:
            lost_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await lost_task

    async def _mark_credential_missing(self, loaded: LoadedTarget) -> None:
        async with self._factory.begin() as session:
            target = await session.get(CollectionTarget, loaded.target.id, with_for_update=True)
            if target is not None and target.config_revision == loaded.target.config_revision:
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
            lock_exists = run.target_id is not None and await redis.exists(
                f"r1:target-lock:{run.target_id}:{run.run_mode.value}"
            )
            dispatch_exists = bool(
                run.dispatch_identity
                and await redis.exists(f"r1:dispatch:{dispatch_task_id(run.dispatch_identity)}")
            )
            if run.target_id is None or lock_exists or dispatch_exists:
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
