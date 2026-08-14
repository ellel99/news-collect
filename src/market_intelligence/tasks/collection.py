from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from celery import Task
from redis.asyncio import Redis

from market_intelligence.collection.contracts import CollectionTarget
from market_intelligence.collection.control_plane import (
    CollectionControlPlaneWorker,
    TargetDispatch,
    TargetScheduler,
    dispatch_task_id,
)
from market_intelligence.collection.locking import retry_marker_key
from market_intelligence.collection.registry import build_fake_registry
from market_intelligence.collection.runner import CollectionRunner, recover_stale_runs
from market_intelligence.collection.scheduler import DispatchRequest, dispatch_due_targets
from market_intelligence.collection.target_configs import build_operation_registry
from market_intelligence.collection.target_repository import TargetRepository
from market_intelligence.core.config import get_settings
from market_intelligence.db.session import create_engine, create_session_factory
from market_intelligence.providers.http_transport import HttpxProviderTransport
from market_intelligence.tasks.celery_app import celery_app


def _target_to_dict(target: CollectionTarget) -> dict[str, Any]:
    return {
        "source_id": str(target.source_id),
        "source_account_id": (
            str(target.source_account_id) if target.source_account_id is not None else None
        ),
        "source_type": target.source_type,
        "access_method": target.access_method,
        "retention_class": target.retention_class,
        "collection_options": dict(target.collection_options),
    }


def _target_from_dict(value: dict[str, Any]) -> CollectionTarget:
    account_id = value.get("source_account_id")
    return CollectionTarget(
        source_id=UUID(value["source_id"]),
        source_account_id=UUID(account_id) if account_id else None,
        source_type=str(value["source_type"]),
        access_method=str(value["access_method"]),
        retention_class=str(value["retention_class"]),
        collection_options=dict(value.get("collection_options", {})),
    )


async def _dispatch() -> int:
    settings = get_settings()
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)

    async def enqueue(request: DispatchRequest) -> None:
        run_target.apply_async(
            kwargs={"target_data": _target_to_dict(request.target)},
            task_id=request.task_id,
        )

    try:
        requests = await dispatch_due_targets(
            factory,
            build_fake_registry(),
            redis,
            enqueue,
            execution_window_seconds=(
                settings.COLLECTION_STALE_RUN_AFTER_SECONDS
                + settings.COLLECTION_TASK_DEADLINE_SECONDS
                + settings.COLLECTION_MAX_RETRY_AFTER_SECONDS
            ),
        )
        return len(requests)
    finally:
        await redis.aclose()
        await engine.dispose()


@celery_app.task(name="collection.dispatch_due_targets")  # type: ignore[untyped-decorator]
def dispatch_due_targets_task() -> dict[str, int]:
    return {"dispatched": asyncio.run(_dispatch())}


async def _run(
    target_data: dict[str, Any],
    collection_run_id: str | None,
    attempt: int,
) -> tuple[dict[str, Any], float | None]:
    settings = get_settings()
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        if collection_run_id is not None:
            await redis.delete(retry_marker_key(collection_run_id))
        outcome = await CollectionRunner(factory, redis, build_fake_registry(), settings).run(
            _target_from_dict(target_data),
            collection_run_id=UUID(collection_run_id) if collection_run_id else None,
            attempt=attempt,
        )
        result = {
            "collection_run_id": (
                str(outcome.collection_run_id) if outcome.collection_run_id else None
            ),
            "status": outcome.status,
        }
        return result, outcome.retry_delay
    finally:
        await redis.aclose()
        await engine.dispose()


async def _mark_retry(collection_run_id: str, delay: float) -> None:
    settings = get_settings()
    redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        ttl = max(
            int(delay) + settings.COLLECTION_TASK_DEADLINE_SECONDS,
            settings.COLLECTION_STALE_RUN_AFTER_SECONDS,
        )
        await redis.set(retry_marker_key(collection_run_id), "scheduled", ex=ttl)
    finally:
        await redis.aclose()


@celery_app.task(bind=True, name="collection.run_target")  # type: ignore[untyped-decorator]
def run_target(
    self: Task,
    target_data: dict[str, Any],
    collection_run_id: str | None = None,
    attempt: int = 0,
) -> dict[str, Any]:
    result, retry_delay = asyncio.run(_run(target_data, collection_run_id, attempt))
    if retry_delay is not None:
        run_id = str(result["collection_run_id"])
        asyncio.run(_mark_retry(run_id, retry_delay))
        raise self.retry(
            kwargs={
                "target_data": target_data,
                "collection_run_id": run_id,
                "attempt": attempt + 1,
            },
            countdown=retry_delay,
            max_retries=get_settings().COLLECTION_MAX_RETRIES,
        )
    return result


async def _recover() -> int:
    settings = get_settings()
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        return await recover_stale_runs(factory, redis, settings.COLLECTION_STALE_RUN_AFTER_SECONDS)
    finally:
        await redis.aclose()
        await engine.dispose()


@celery_app.task(name="collection.recover_stale_runs")  # type: ignore[untyped-decorator]
def recover_stale_runs_task() -> dict[str, int]:
    return {"recovered": asyncio.run(_recover())}


async def _dispatch_control_plane() -> int:
    settings = get_settings()
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        repository = TargetRepository(factory, build_operation_registry())
        requests = await TargetScheduler(repository, redis).claim_due(datetime.now(UTC))
        for request in requests:
            run_collection_target.apply_async(
                kwargs=request.payload(),
                task_id=dispatch_task_id(request.dispatch_id),
            )
        return len(requests)
    finally:
        await redis.aclose()
        await engine.dispose()


@celery_app.task(name="collection.control_plane.dispatch")  # type: ignore[untyped-decorator]
def dispatch_collection_targets() -> dict[str, int]:
    """Inactive until an explicit production cutover changes Beat authority."""
    return {"dispatched": asyncio.run(_dispatch_control_plane())}


@celery_app.task(name="collection.control_plane.run_target")  # type: ignore[untyped-decorator]
def run_collection_target(
    target_id: str,
    config_revision: int,
    scheduled_slot: int,
    run_mode: str,
    dispatch_id: str,
) -> dict[str, Any]:
    async def run() -> dict[str, Any]:
        settings = get_settings()
        engine = create_engine(settings)
        factory = create_session_factory(engine)
        redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)
        try:
            worker = CollectionControlPlaneWorker(
                factory,
                TargetRepository(factory, build_operation_registry()),
                redis,
                HttpxProviderTransport(),
                environ=os.environ,
            )
            outcome = await worker.execute(
                TargetDispatch(
                    UUID(target_id),
                    config_revision,
                    scheduled_slot,
                    run_mode,
                    dispatch_id,
                )
            )
            return {
                "target_id": str(outcome.target_id),
                "run_id": str(outcome.run_id) if outcome.run_id else None,
                "status": outcome.status,
                "safe_error": outcome.safe_error,
            }
        finally:
            await redis.aclose()
            await engine.dispose()

    return asyncio.run(run())
