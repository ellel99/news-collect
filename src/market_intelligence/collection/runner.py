from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from random import Random
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from market_intelligence.collection.contracts import (
    CollectionAdapter,
    CollectionTarget,
    CursorSnapshot,
    FetchBatch,
    FetchRequest,
)
from market_intelligence.collection.errors import ClassifiedCollectionError, CollectionErrorCode
from market_intelligence.collection.locking import (
    TargetLock,
    retry_marker_key,
    target_lock_key,
)
from market_intelligence.collection.provider_adapter import (
    ProviderCollectionAdapter,
    ProviderResultObserver,
)
from market_intelligence.collection.registry import AdapterRegistry
from market_intelligence.collection.retry import RetryPolicy
from market_intelligence.core.config import Settings
from market_intelligence.db.models import (
    AuthorizationStatus,
    CollectionCursor,
    CollectionRun,
    CollectionRunStatus,
    ParseStatus,
    RawItem,
    Source,
    SourceAccount,
)
from market_intelligence.providers.contracts import ProviderTransport
from market_intelligence.providers.registry import ProviderAdapterRegistry


@dataclass(frozen=True, slots=True)
class RunOutcome:
    collection_run_id: UUID | None
    status: str
    retry_delay: float | None = None


class CollectionRunner:
    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        redis: Redis,
        registry: AdapterRegistry,
        settings: Settings,
        *,
        random_source: Random | None = None,
        provider_registry: ProviderAdapterRegistry | None = None,
        provider_transport: ProviderTransport | None = None,
        provider_result_observer: ProviderResultObserver | None = None,
        max_batches: int | None = None,
    ) -> None:
        self.factory = factory
        self.redis = redis
        self.registry = registry
        self.settings = settings
        self.random = random_source or Random()
        self.provider_registry = provider_registry
        self.provider_transport = provider_transport
        self.provider_result_observer = provider_result_observer
        if max_batches is not None and max_batches < 1:
            raise ValueError("max_batches must be positive")
        self.max_batches = max_batches
        self.retry_policy = RetryPolicy(
            settings.COLLECTION_MAX_RETRIES,
            settings.COLLECTION_RETRY_BASE_SECONDS,
            settings.COLLECTION_RETRY_MAX_SECONDS,
            settings.COLLECTION_MAX_RETRY_AFTER_SECONDS,
        )

    async def run(
        self,
        target: CollectionTarget,
        *,
        collection_run_id: UUID | None = None,
        attempt: int = 0,
    ) -> RunOutcome:
        await self._validate_target(target)
        lock = TargetLock.for_target(self.redis, target, self.settings.COLLECTION_LOCK_TTL_SECONDS)
        if not await lock.acquire():
            return RunOutcome(collection_run_id, "locked")
        lost = asyncio.Event()
        renewal = asyncio.create_task(self._renew_lock(lock, lost))
        run_id = collection_run_id
        try:
            run_id = run_id or await self._create_run(target)
            adapter = self._resolve_adapter(target.access_method)
            deadline = datetime.now(UTC) + timedelta(
                seconds=self.settings.COLLECTION_TASK_DEADLINE_SECONDS
            )
            completed_batches = 0
            while True:
                cursor = await self._load_cursor(target, adapter.cursor_type)
                request = FetchRequest(
                    target=target,
                    cursor=cursor,
                    batch_limit=self.settings.COLLECTION_BATCH_LIMIT,
                    deadline_at=deadline,
                )
                try:
                    async with asyncio.timeout(
                        min(
                            self.settings.COLLECTION_ADAPTER_TIMEOUT_SECONDS,
                            max((deadline - datetime.now(UTC)).total_seconds(), 0),
                        )
                    ):
                        batch = await adapter.fetch(request)
                except TimeoutError as exc:
                    raise ClassifiedCollectionError(
                        CollectionErrorCode.TIMEOUT, "adapter deadline exceeded"
                    ) from exc
                if lost.is_set():
                    raise ClassifiedCollectionError(
                        CollectionErrorCode.LOCK_LOST, "target lock ownership lost"
                    )
                await self._persist_checkpoint(target, run_id, cursor, batch, adapter)
                completed_batches += 1
                if not batch.has_more or (
                    self.max_batches is not None and completed_batches >= self.max_batches
                ):
                    await self._finish_success(target, run_id)
                    return RunOutcome(run_id, CollectionRunStatus.SUCCEEDED.value)
        except ClassifiedCollectionError as error:
            if run_id is None:
                raise
            if self.retry_policy.should_retry(error, attempt):
                delay = self.retry_policy.delay(error, attempt, self.random)
                await self._record_retry(run_id, error, attempt + 1)
                return RunOutcome(run_id, "retry", delay)
            await self._finish_failure(target, run_id, error)
            return RunOutcome(run_id, "failed")
        except SQLAlchemyError:
            if run_id is None:
                raise
            database_error = ClassifiedCollectionError(
                CollectionErrorCode.DATABASE_UNAVAILABLE, "database operation failed"
            )
            if self.retry_policy.should_retry(database_error, attempt):
                delay = self.retry_policy.delay(database_error, attempt, self.random)
                await self._record_retry(run_id, database_error, attempt + 1)
                return RunOutcome(run_id, "retry", delay)
            await self._finish_failure(target, run_id, database_error)
            return RunOutcome(run_id, "failed")
        except asyncio.CancelledError:
            if run_id is not None:
                cancelled = ClassifiedCollectionError(
                    CollectionErrorCode.CANCELLED, "collection task cancelled"
                )
                await self._finish_failure(target, run_id, cancelled)
            raise
        except Exception:
            if run_id is None:
                raise
            unknown = ClassifiedCollectionError(
                CollectionErrorCode.UNKNOWN, "unclassified collection failure"
            )
            await self._finish_failure(target, run_id, unknown)
            return RunOutcome(run_id, "failed")
        finally:
            renewal.cancel()
            await asyncio.gather(renewal, return_exceptions=True)
            await lock.release()

    async def _validate_target(self, target: CollectionTarget) -> None:
        if not self._supports_access_method(target.access_method):
            raise ClassifiedCollectionError(
                CollectionErrorCode.CONFIG_INVALID, "access method is not registered"
            )
        async with self.factory() as session:
            source = await session.get(Source, target.source_id)
            if (
                source is None
                or not source.enabled
                or source.access_method != target.access_method
                or source.authorization_status
                not in {AuthorizationStatus.AUTHORIZED, AuthorizationStatus.IMPLEMENTED}
            ):
                raise ClassifiedCollectionError(
                    CollectionErrorCode.CONFIG_INVALID, "source is not authorized for collection"
                )
            if target.source_account_id is not None:
                account = await session.get(SourceAccount, target.source_account_id)
                if account is None or account.source_id != target.source_id or not account.enabled:
                    raise ClassifiedCollectionError(
                        CollectionErrorCode.CONFIG_INVALID,
                        "source account is missing, mismatched, or disabled",
                    )
            else:
                account_exists = await session.scalar(
                    select(SourceAccount.id)
                    .where(SourceAccount.source_id == target.source_id)
                    .limit(1)
                )
                if account_exists is not None:
                    raise ClassifiedCollectionError(
                        CollectionErrorCode.CONFIG_INVALID,
                        "source-level collection is forbidden when accounts exist",
                    )

    def _supports_access_method(self, access_method: str) -> bool:
        if access_method == "fake":
            return self.registry.supports(access_method)
        return (
            self.provider_registry is not None
            and self.provider_transport is not None
            and self.provider_registry.supports(access_method)
        )

    def _resolve_adapter(self, access_method: str) -> CollectionAdapter:
        if access_method == "fake":
            return self.registry.resolve(access_method)
        if self.provider_registry is None or self.provider_transport is None:
            raise ClassifiedCollectionError(
                CollectionErrorCode.CONFIG_INVALID,
                "provider adapter integration is not configured",
            )
        return ProviderCollectionAdapter(
            self.provider_registry.get(access_method),
            self.provider_transport,
            self.provider_result_observer,
        )

    async def _create_run(self, target: CollectionTarget) -> UUID:
        async with self.factory.begin() as session:
            run = CollectionRun(
                source_id=target.source_id,
                source_account_id=target.source_account_id,
                started_at=datetime.now(UTC),
                status=CollectionRunStatus.RUNNING,
            )
            session.add(run)
            await session.flush()
            return run.id

    async def _load_cursor(
        self, target: CollectionTarget, cursor_type: str | None
    ) -> CursorSnapshot:
        if target.source_account_id is None or cursor_type is None:
            return CursorSnapshot(cursor_type, None, None)
        async with self.factory() as session:
            cursor = await session.scalar(
                select(CollectionCursor).where(
                    CollectionCursor.source_account_id == target.source_account_id,
                    CollectionCursor.cursor_type == cursor_type,
                )
            )
            if cursor is None:
                return CursorSnapshot(cursor_type, None, None)
            return CursorSnapshot(cursor.cursor_type, cursor.cursor_value, cursor.last_published_at)

    async def _persist_checkpoint(
        self,
        target: CollectionTarget,
        run_id: UUID,
        snapshot: CursorSnapshot,
        batch: FetchBatch,
        adapter: CollectionAdapter,
    ) -> None:
        if batch.next_cursor is not None and not adapter.is_cursor_successor(
            snapshot.cursor_value, batch.next_cursor
        ):
            raise ClassifiedCollectionError(
                CollectionErrorCode.CONTRACT_INVALID, "cursor is not a direct successor"
            )
        if (
            snapshot.last_published_at is not None
            and batch.last_published_at is not None
            and batch.last_published_at < snapshot.last_published_at
        ):
            raise ClassifiedCollectionError(
                CollectionErrorCode.CONTRACT_INVALID, "published watermark moved backward"
            )
        async with self.factory.begin() as session:
            for item in batch.items:
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
            run = await session.get(CollectionRun, run_id, with_for_update=True)
            if run is None or run.status != CollectionRunStatus.RUNNING:
                raise ClassifiedCollectionError(
                    CollectionErrorCode.CONTRACT_INVALID, "collection run is not running"
                )
            run.fetched_count += len(batch.items)
            if (
                target.source_account_id is not None
                and snapshot.cursor_type is not None
                and batch.next_cursor is not None
            ):
                cursor = await session.scalar(
                    select(CollectionCursor)
                    .where(
                        CollectionCursor.source_account_id == target.source_account_id,
                        CollectionCursor.cursor_type == snapshot.cursor_type,
                    )
                    .with_for_update()
                )
                if cursor is None:
                    cursor = CollectionCursor(
                        source_account_id=target.source_account_id,
                        cursor_type=snapshot.cursor_type,
                    )
                    session.add(cursor)
                elif cursor.cursor_value != snapshot.cursor_value:
                    raise ClassifiedCollectionError(
                        CollectionErrorCode.CONTRACT_INVALID, "cursor snapshot is stale"
                    )
                cursor.cursor_value = batch.next_cursor
                cursor.last_published_at = batch.last_published_at or snapshot.last_published_at

    async def _finish_success(self, target: CollectionTarget, run_id: UUID) -> None:
        async with self.factory.begin() as session:
            run = await session.get(CollectionRun, run_id, with_for_update=True)
            source = await session.get(Source, target.source_id, with_for_update=True)
            if run is None or source is None:
                return
            now = datetime.now(UTC)
            run.status = CollectionRunStatus.SUCCEEDED
            run.finished_at = now
            run.error_code = None
            run.error_message_redacted = None
            if target.source_account_id is None:
                source.last_success_at = now
                source.consecutive_failures = 0

    async def _record_retry(
        self, run_id: UUID, error: ClassifiedCollectionError, retry_count: int
    ) -> None:
        async with self.factory.begin() as session:
            run = await session.get(CollectionRun, run_id, with_for_update=True)
            if run is not None:
                run.retry_count = retry_count
                run.error_count += 1
                run.error_code = error.code.value
                run.error_message_redacted = error.redacted_detail

    async def _finish_failure(
        self,
        target: CollectionTarget,
        run_id: UUID,
        error: ClassifiedCollectionError,
    ) -> None:
        async with self.factory.begin() as session:
            run = await session.get(CollectionRun, run_id, with_for_update=True)
            source = await session.get(Source, target.source_id, with_for_update=True)
            if run is None or source is None:
                return
            run.status = (
                CollectionRunStatus.PARTIAL if run.fetched_count > 0 else CollectionRunStatus.FAILED
            )
            run.finished_at = datetime.now(UTC)
            run.error_count += 1
            run.error_code = error.code.value
            run.error_message_redacted = error.redacted_detail
            source.consecutive_failures += 1

    async def _renew_lock(self, lock: TargetLock, lost: asyncio.Event) -> None:
        try:
            while True:
                await asyncio.sleep(lock.ttl_seconds / 2)
                if not await lock.renew():
                    lost.set()
                    return
        except asyncio.CancelledError:
            raise


async def recover_stale_runs(
    factory: async_sessionmaker[AsyncSession],
    redis: Redis,
    stale_after_seconds: int,
    *,
    now: datetime | None = None,
) -> int:
    current = now or datetime.now(UTC)
    threshold = current - timedelta(seconds=stale_after_seconds)
    recovered = 0
    async with factory.begin() as session:
        runs = list(
            (
                await session.scalars(
                    select(CollectionRun)
                    .where(
                        CollectionRun.status == CollectionRunStatus.RUNNING,
                        CollectionRun.started_at < threshold,
                    )
                    .with_for_update(skip_locked=True)
                )
            ).all()
        )
        for run in runs:
            target = CollectionTarget(run.source_id, run.source_account_id, "", "fake", "", {})
            if await redis.exists(target_lock_key(target), retry_marker_key(str(run.id))):
                continue
            run.status = CollectionRunStatus.FAILED
            run.finished_at = current
            run.error_count += 1
            run.error_code = CollectionErrorCode.STALE_RUN.value
            run.error_message_redacted = "stale collection run recovered"
            source = await session.get(Source, run.source_id, with_for_update=True)
            if source is not None:
                source.consecutive_failures += 1
            recovered += 1
    return recovered
