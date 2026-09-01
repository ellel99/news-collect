"""CAS repository and exact worker reload for collection targets."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from market_intelligence.collection.target_configs import OperationContract, OperationRegistry
from market_intelligence.db.models import (
    AuditLog,
    CollectionCursor,
    CollectionCursorStrategy,
    CollectionMode,
    CollectionRun,
    CollectionRunStatus,
    CollectionTarget,
    CollectionTargetHealthStatus,
    CollectionTargetStatus,
    Source,
    SourceAccount,
)
from market_intelligence.safe_projection.contracts import canonical_projection_hash


class TargetRepositoryError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class LoadedTarget:
    target: CollectionTarget
    source: Source
    account: SourceAccount | None
    contract: OperationContract
    config: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class DueTarget:
    target_id: UUID
    effective_due_at: datetime
    priority: int
    retry_due: bool


@dataclass(frozen=True, slots=True)
class PendingContinuationToken:
    cursor_version: int
    run_mode: str
    bound_run_id: UUID
    continuation_hash: str


_TRANSITIONS = {
    CollectionTargetStatus.DRAFT: {
        CollectionTargetStatus.PAUSED,
        CollectionTargetStatus.BLOCKED,
    },
    CollectionTargetStatus.PAUSED: {
        CollectionTargetStatus.ACTIVE,
        CollectionTargetStatus.BLOCKED,
        CollectionTargetStatus.RETIRED,
    },
    CollectionTargetStatus.ACTIVE: {
        CollectionTargetStatus.PAUSED,
        CollectionTargetStatus.BLOCKED,
        CollectionTargetStatus.RETIRED,
    },
    CollectionTargetStatus.BLOCKED: {
        CollectionTargetStatus.PAUSED,
        CollectionTargetStatus.RETIRED,
    },
    CollectionTargetStatus.RETIRED: set(),
}


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
            if target.source_account_id is None:
                account_count = int(
                    await session.scalar(
                        select(func.count())
                        .select_from(SourceAccount)
                        .where(SourceAccount.source_id == target.source_id)
                    )
                    or 0
                )
                if account_count:
                    raise TargetRepositoryError("source_level_target_has_accounts")
            if source is None or not eligible(source, account, target):
                raise TargetRepositoryError("target_not_eligible")
            contract = self._registry.resolve(
                source.access_method,
                target.operation_key,
                target.operation_config_version,
                target.provider_contract_version,
            )
            if target.legacy_cursor_type != contract.legacy_cursor_type:
                raise TargetRepositoryError("target_legacy_identity_mismatch")
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
        allowed = {
            "operation_config",
            "operation_config_version",
            "provider_contract_version",
            "status",
            "cadence_seconds",
            "batch_limit",
            "max_requests_per_run",
            "max_pages_per_run",
            "max_response_bytes",
            "request_timeout_seconds",
            "max_runtime_seconds",
            "cursor_strategy",
            "cursor_version",
            "collection_mode",
            "backfill_policy",
            "revision_policy",
            "rate_limit_group",
            "priority",
        }
        if set(safe) - allowed:
            raise TargetRepositoryError("target_revision_fields_invalid")
        safe["config_revision"] = expected_revision + 1
        safe["health_status"] = CollectionTargetHealthStatus.UNKNOWN
        safe["updated_at"] = datetime.now(UTC)
        async with self._factory.begin() as session:
            current = await session.scalar(
                select(CollectionTarget)
                .where(
                    CollectionTarget.id == target_id,
                    CollectionTarget.config_revision == expected_revision,
                )
                .with_for_update()
            )
            if current is None:
                raise TargetRepositoryError("target_revision_conflict")
            running = await session.scalar(
                select(CollectionRun.id).where(
                    CollectionRun.target_id == target_id,
                    CollectionRun.status == CollectionRunStatus.RUNNING,
                )
            )
            if running is not None:
                raise TargetRepositoryError("target_revision_in_flight")
            cursors = tuple(
                await session.scalars(
                    select(CollectionCursor)
                    .where(CollectionCursor.target_id == target_id)
                    .with_for_update()
                )
            )
            if any(cursor.continuation for cursor in cursors):
                raise TargetRepositoryError("target_revision_pending_continuation")
            proposed_status = safe.get("status", current.status)
            if isinstance(proposed_status, str):
                try:
                    proposed_status = CollectionTargetStatus(proposed_status)
                except ValueError:
                    raise TargetRepositoryError("target_status_invalid") from None
                safe["status"] = proposed_status
            if (
                proposed_status != current.status
                and proposed_status not in _TRANSITIONS[current.status]
            ):
                raise TargetRepositoryError("target_status_transition_invalid")
            source = await session.get(Source, current.source_id)
            if source is None:
                raise TargetRepositoryError("target_source_not_found")
            contract = self._registry.resolve(
                source.access_method,
                current.operation_key,
                int(safe.get("operation_config_version", current.operation_config_version)),
                int(safe.get("provider_contract_version", current.provider_contract_version)),
            )
            if current.legacy_cursor_type != contract.legacy_cursor_type:
                raise TargetRepositoryError("target_legacy_identity_mismatch")
            self._registry.validate(
                contract,
                safe.get("operation_config", current.operation_config),
                batch_limit=int(safe.get("batch_limit", current.batch_limit)),
                max_requests=int(safe.get("max_requests_per_run", current.max_requests_per_run)),
                max_pages=int(safe.get("max_pages_per_run", current.max_pages_per_run)),
            )
            cursor_strategy = safe.get("cursor_strategy", current.cursor_strategy)
            collection_mode = safe.get("collection_mode", current.collection_mode)
            try:
                cursor_strategy = CollectionCursorStrategy(cursor_strategy)
                collection_mode = CollectionMode(collection_mode)
            except ValueError:
                raise TargetRepositoryError("target_operation_semantics_invalid") from None
            if (
                cursor_strategy is not contract.cursor_strategy
                or collection_mode is not contract.collection_mode
            ):
                raise TargetRepositoryError("target_operation_semantics_invalid")
            safe["cursor_strategy"] = cursor_strategy
            safe["collection_mode"] = collection_mode
            rate_limit_group = str(safe.get("rate_limit_group", current.rate_limit_group))
            if re.fullmatch(r"[a-z0-9][a-z0-9:._-]{0,159}", rate_limit_group) is None:
                raise TargetRepositoryError("target_rate_limit_group_invalid")
            request_timeout = int(
                safe.get("request_timeout_seconds", current.request_timeout_seconds)
            )
            runtime = int(safe.get("max_runtime_seconds", current.max_runtime_seconds))
            response_bytes = int(safe.get("max_response_bytes", current.max_response_bytes))
            cadence = int(safe.get("cadence_seconds", current.cadence_seconds))
            if not (1 <= request_timeout <= 60 and request_timeout <= runtime <= 900):
                raise TargetRepositoryError("target_budget_invalid")
            if not (1024 <= response_bytes <= 10_000_000 and 1 <= cadence <= 86_400):
                raise TargetRepositoryError("target_budget_invalid")
            if proposed_status is CollectionTargetStatus.ACTIVE:
                account = (
                    await session.get(SourceAccount, current.source_account_id)
                    if current.source_account_id is not None
                    else None
                )
                if current.source_account_id is None:
                    account_count = int(
                        await session.scalar(
                            select(func.count())
                            .select_from(SourceAccount)
                            .where(SourceAccount.source_id == current.source_id)
                        )
                        or 0
                    )
                    if account_count:
                        raise TargetRepositoryError("source_level_target_has_accounts")
                if (
                    not source.enabled
                    or source.authorization_status.value not in {"authorized", "implemented"}
                    or (
                        account is not None
                        and (
                            not account.enabled
                            or account.identity_status.value != "verified"
                            or account.source_id != source.id
                        )
                    )
                ):
                    raise TargetRepositoryError("target_not_eligible")
            now = datetime.now(UTC)
            safe["retired_at"] = now if proposed_status is CollectionTargetStatus.RETIRED else None
            safe["next_retry_at"] = None
            safe["next_due_at"] = now
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
            session.add(
                AuditLog(
                    actor_type="system",
                    actor_id=None,
                    action="collection_target_revised",
                    target_type="collection_target",
                    target_id=target_id,
                    before={"config_revision": expected_revision},
                    after={
                        "config_revision": int(changed),
                        "status": proposed_status.value,
                    },
                )
            )
            return int(changed)

    async def pending_continuations(
        self, target_id: UUID, expected_revision: int
    ) -> tuple[PendingContinuationToken, ...]:
        """Return value-free optimistic tokens for an explicit abandon review."""

        async with self._factory() as session:
            target = await session.get(CollectionTarget, target_id)
            if target is None or target.config_revision != expected_revision:
                raise TargetRepositoryError("target_revision_conflict")
            cursors = tuple(
                await session.scalars(
                    select(CollectionCursor)
                    .where(
                        CollectionCursor.target_id == target_id,
                        CollectionCursor.continuation.is_not(None),
                    )
                    .order_by(CollectionCursor.cursor_version, CollectionCursor.run_mode)
                )
            )
            return tuple(self._continuation_token(cursor) for cursor in cursors)

    async def pause_for_continuation_abandon(self, target_id: UUID, expected_revision: int) -> None:
        """Explicitly pause execution without changing the continuation lineage revision."""

        async with self._factory.begin() as session:
            target = await session.scalar(
                select(CollectionTarget)
                .where(
                    CollectionTarget.id == target_id,
                    CollectionTarget.config_revision == expected_revision,
                )
                .with_for_update()
            )
            if target is None:
                raise TargetRepositoryError("target_revision_conflict")
            running = await session.scalar(
                select(CollectionRun.id).where(
                    CollectionRun.target_id == target_id,
                    CollectionRun.status == CollectionRunStatus.RUNNING,
                )
            )
            if running is not None:
                raise TargetRepositoryError("target_revision_in_flight")
            if target.status not in {
                CollectionTargetStatus.ACTIVE,
                CollectionTargetStatus.PAUSED,
            }:
                raise TargetRepositoryError("target_abandon_pause_invalid")
            if target.status is CollectionTargetStatus.PAUSED:
                return
            target.status = CollectionTargetStatus.PAUSED
            target.next_retry_at = None
            session.add(
                AuditLog(
                    actor_type="system",
                    action="collection_target_paused_for_continuation_abandon",
                    target_type="collection_target",
                    target_id=target_id,
                    before={"config_revision": expected_revision, "status": "active"},
                    after={"config_revision": expected_revision, "status": "paused"},
                )
            )

    async def abandon_pending_continuations(
        self,
        target_id: UUID,
        expected_revision: int,
        expected: tuple[PendingContinuationToken, ...],
        *,
        reason_code: str,
    ) -> int:
        """Atomically abandon reviewed recovery state without changing checkpoints."""

        if reason_code not in {
            "continuation_unrecoverable",
            "operator_reviewed_reset",
            "provider_continuation_invalid",
        }:
            raise TargetRepositoryError("continuation_abandon_reason_invalid")
        async with self._factory.begin() as session:
            target = await session.scalar(
                select(CollectionTarget)
                .where(
                    CollectionTarget.id == target_id,
                    CollectionTarget.config_revision == expected_revision,
                )
                .with_for_update()
            )
            if target is None:
                raise TargetRepositoryError("target_revision_conflict")
            cursors = tuple(
                await session.scalars(
                    select(CollectionCursor)
                    .where(CollectionCursor.target_id == target_id)
                    .order_by(CollectionCursor.cursor_version, CollectionCursor.run_mode)
                    .with_for_update()
                )
            )
            pending = tuple(cursor for cursor in cursors if cursor.continuation is not None)
            if not pending:
                return 0
            actual = tuple(self._continuation_token(cursor) for cursor in pending)
            if actual != expected:
                raise TargetRepositoryError("target_continuation_changed")
            run_ids = tuple(token.bound_run_id for token in actual)
            all_runs = tuple(
                await session.scalars(
                    select(CollectionRun)
                    .where(CollectionRun.target_id == target_id)
                    .order_by(CollectionRun.id)
                    .with_for_update()
                )
            )
            if any(run.status == CollectionRunStatus.RUNNING for run in all_runs):
                raise TargetRepositoryError("target_revision_in_flight")
            runs = {row.id: row for row in all_runs if row.id in run_ids}
            if len(runs) != len(set(run_ids)):
                raise TargetRepositoryError("target_continuation_run_missing")
            if target.status is not CollectionTargetStatus.PAUSED:
                raise TargetRepositoryError("target_continuation_abandon_requires_paused")
            for cursor, token in zip(pending, actual, strict=True):
                run = runs[token.bound_run_id]
                window_hash = (
                    canonical_projection_hash(run.resolved_window)
                    if run.resolved_window is not None
                    else None
                )
                session.add(
                    AuditLog(
                        actor_type="system",
                        action="collection_continuation_abandoned",
                        target_type="collection_target",
                        target_id=target_id,
                        before=None,
                        after={
                            "config_revision": expected_revision,
                            "cursor_version": cursor.cursor_version,
                            "run_mode": cursor.run_mode.value,
                            "bound_run_id": str(token.bound_run_id),
                            "continuation_hash": token.continuation_hash,
                            "resolved_window_hash": window_hash,
                            "operation_config_hash": run.operation_config_hash,
                            "reason_code": reason_code,
                        },
                    )
                )
                cursor.continuation = None
                cursor.continuation_run_id = None
            return len(pending)

    @staticmethod
    def _continuation_token(cursor: CollectionCursor) -> PendingContinuationToken:
        if cursor.continuation is None or cursor.continuation_run_id is None:
            raise TargetRepositoryError("target_continuation_binding_invalid")
        return PendingContinuationToken(
            cursor_version=cursor.cursor_version,
            run_mode=cursor.run_mode.value,
            bound_run_id=cursor.continuation_run_id,
            continuation_hash=canonical_projection_hash(cursor.continuation),
        )

    async def due_page(
        self,
        now: datetime,
        limit: int,
        after: tuple[datetime, int, UUID] | None = None,
    ) -> tuple[DueTarget, ...]:
        effective_due = func.coalesce(CollectionTarget.next_retry_at, CollectionTarget.next_due_at)
        async with self._factory() as session:
            query = (
                select(
                    CollectionTarget.id,
                    effective_due.label("effective_due"),
                    CollectionTarget.priority,
                    CollectionTarget.next_retry_at.is_not(None).label("retry_due"),
                )
                .where(
                    CollectionTarget.status == CollectionTargetStatus.ACTIVE,
                    effective_due <= now,
                    ~(
                        (CollectionTarget.provider_contract_version == 2)
                        & (
                            CollectionTarget.operation_config["window_mode"].astext
                            == "fixed_window"
                        )
                    ),
                )
                .order_by(effective_due, CollectionTarget.priority, CollectionTarget.id)
                .limit(limit)
            )
            if after is not None:
                due, priority, target_id = after
                query = query.where(
                    or_(
                        effective_due > due,
                        (effective_due == due) & (CollectionTarget.priority > priority),
                        (effective_due == due)
                        & (CollectionTarget.priority == priority)
                        & (CollectionTarget.id > target_id),
                    )
                )
            rows = (await session.execute(query)).all()
            return tuple(
                DueTarget(row.id, row.effective_due, row.priority, row.retry_due) for row in rows
            )
