"""Explicit, deterministic Phase 2 legacy target creation and provenance backfill."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from market_intelligence.collection.target_configs import (
    OperationContract,
    OperationRegistry,
    TargetConfigError,
)
from market_intelligence.db.models import (
    AuditLog,
    CollectionBackfillPolicy,
    CollectionRevisionPolicy,
    CollectionTarget,
    CollectionTargetHealthStatus,
    CollectionTargetStatus,
    Source,
    SourceAccount,
)


class Phase2MigrationError(RuntimeError):
    """Value-free Phase 2 failure."""


@dataclass(frozen=True, slots=True)
class Phase2Report:
    source_count: int
    account_count: int
    candidate_count: int
    created_count: int
    existing_count: int
    blocked_count: int
    runs_backfilled: int
    cursors_backfilled: int


@dataclass(frozen=True, slots=True)
class _Candidate:
    source: Source
    account: SourceAccount | None
    contract: OperationContract | None
    config: dict[str, Any]
    valid: bool


class LegacyTargetPhase2Service:
    """Run Phase 2 explicitly; Alembic upgrade never invokes this service."""

    def __init__(
        self, factory: async_sessionmaker[AsyncSession], registry: OperationRegistry
    ) -> None:
        self._factory, self._registry = factory, registry

    async def run(self) -> Phase2Report:
        async with self._factory.begin() as session:
            await self._preflight(session)
            sources = tuple(
                await session.scalars(select(Source).order_by(Source.id).with_for_update())
            )
            accounts = tuple(
                await session.scalars(select(SourceAccount).order_by(SourceAccount.id))
            )
            by_source: dict[Any, list[SourceAccount]] = {}
            for account in accounts:
                by_source.setdefault(account.source_id, []).append(account)
            candidates: list[_Candidate] = []
            for source in sources:
                source_accounts: list[SourceAccount | None] = list(
                    by_source.get(source.id, [])
                ) or [None]
                candidates.extend(self._candidate(source, account) for account in source_accounts)
            created = existing = blocked = 0
            for candidate in candidates:
                target_key = self._target_key(candidate.source, candidate.account)
                target = await session.scalar(
                    select(CollectionTarget).where(CollectionTarget.target_key == target_key)
                )
                if target is not None:
                    existing += 1
                    continue
                target = self._build_target(candidate, target_key)
                session.add(target)
                await session.flush()
                created += 1
                blocked += int(target.status is CollectionTargetStatus.BLOCKED)
                session.add(
                    AuditLog(
                        actor_type="system",
                        actor_id=None,
                        action="r1_phase2_target_created",
                        target_type="collection_target",
                        target_id=target.id,
                        before=None,
                        after={
                            "status": target.status.value,
                            "mapping_valid": candidate.valid,
                        },
                    )
                )
            before_runs = await self._count_unmapped(session, "collection_runs")
            before_cursors = await self._count_unmapped(session, "collection_cursors")
            await session.execute(text("SELECT set_config('r1.phase2_backfill','on',true)"))
            run_result = await session.execute(
                text("""
                    UPDATE collection_runs r SET target_id=t.id
                    FROM collection_targets t
                    WHERE r.target_id IS NULL AND r.source_id=t.source_id
                      AND r.source_account_id IS NOT DISTINCT FROM t.source_account_id
                      AND t.legacy_cursor_type IS NOT NULL
                    """)
            )
            runs = int(getattr(run_result, "rowcount", 0))
            cursor_result = await session.execute(
                text("""
                    UPDATE collection_cursors c SET target_id=t.id
                    FROM collection_targets t
                    WHERE c.target_id IS NULL AND c.source_account_id=t.source_account_id
                      AND c.cursor_type=t.legacy_cursor_type
                    """)
            )
            cursors = int(getattr(cursor_result, "rowcount", 0))
            session.add(
                AuditLog(
                    actor_type="system",
                    actor_id=None,
                    action="r1_phase2_reconciliation",
                    target_type="collection_target",
                    target_id=None,
                    before={
                        "unmapped_runs": before_runs,
                        "unmapped_cursors": before_cursors,
                    },
                    after={
                        "created_count": created,
                        "existing_count": existing,
                        "blocked_count": blocked,
                        "runs_backfilled": runs,
                        "cursors_backfilled": cursors,
                    },
                )
            )
            return Phase2Report(
                len(sources),
                len(accounts),
                len(candidates),
                created,
                existing,
                blocked,
                runs,
                cursors,
            )

    async def _preflight(self, session: AsyncSession) -> None:
        mismatch = await session.scalar(
            text("""
            SELECT count(*) FROM raw_items i JOIN collection_runs r ON r.id=i.collection_run_id
            WHERE i.source_id IS DISTINCT FROM r.source_id
               OR i.source_account_id IS DISTINCT FROM r.source_account_id
            """)
        )
        if mismatch:
            raise Phase2MigrationError("phase2_historical_provenance_mismatch")
        ambiguous = await session.scalar(
            text("""
            SELECT count(*) FROM (
              SELECT source_id, source_account_id
              FROM collection_targets
              WHERE legacy_cursor_type IS NOT NULL
              GROUP BY source_id, source_account_id
              HAVING count(*) > 1
            ) candidates
            """)
        )
        if ambiguous:
            raise Phase2MigrationError("phase2_target_mapping_ambiguous")

    def _candidate(self, source: Source, account: SourceAccount | None) -> _Candidate:
        if account is None:
            return _Candidate(source, None, None, {}, False)
        mapping = {
            "marketaux": "news_all",
            "finnhub": "quote",
            "eia": "electricity_retail_sales",
            "sec_edgar": "submissions_recent",
        }
        operation = mapping.get(source.access_method)
        if operation is None:
            return _Candidate(source, account, None, {}, False)
        try:
            contract = self._registry.resolve(source.access_method, operation, 1, 1)
            config = dict(
                self._registry.validate(
                    contract,
                    account.collection_options,
                    batch_limit=contract.batch_ceiling,
                    max_requests=1,
                    max_pages=1,
                )
            )
        except TargetConfigError:
            return _Candidate(source, account, None, {}, False)
        return _Candidate(source, account, contract, config, True)

    @staticmethod
    def _target_key(source: Source, account: SourceAccount | None) -> str:
        identity = f"account.{account.id}" if account else f"source.{source.id}"
        return f"legacy.{source.access_method}.{identity}".lower()

    @staticmethod
    def _build_target(candidate: _Candidate, target_key: str) -> CollectionTarget:
        contract = candidate.contract
        now = datetime.now(UTC)
        if contract is None:
            return CollectionTarget(
                target_key=target_key,
                source_id=candidate.source.id,
                source_account_id=candidate.account.id if candidate.account else None,
                operation_key="unsupported",
                legacy_cursor_type=None,
                operation_config_version=1,
                provider_contract_version=1,
                operation_config={},
                status=CollectionTargetStatus.BLOCKED,
                cadence_seconds=candidate.source.schedule_seconds or 3600,
                batch_limit=1,
                max_requests_per_run=1,
                max_pages_per_run=1,
                max_response_bytes=1_000_000,
                request_timeout_seconds=30,
                max_runtime_seconds=120,
                cursor_strategy="strict_incremental",
                cursor_version=1,
                collection_mode="incremental",
                backfill_policy=CollectionBackfillPolicy.DISABLED,
                revision_policy=CollectionRevisionPolicy.IGNORE,
                rate_limit_group="blocked:unsupported",
                next_due_at=now,
                health_status=CollectionTargetHealthStatus.BLOCKED,
                last_error_code="legacy_mapping_invalid",
            )
        return CollectionTarget(
            target_key=target_key,
            source_id=candidate.source.id,
            source_account_id=candidate.account.id if candidate.account else None,
            operation_key=contract.operation_key,
            legacy_cursor_type=contract.legacy_cursor_type,
            operation_config_version=contract.operation_config_version,
            provider_contract_version=contract.provider_contract_version,
            operation_config=candidate.config,
            status=CollectionTargetStatus.PAUSED,
            cadence_seconds=candidate.source.schedule_seconds or 3600,
            batch_limit=contract.batch_ceiling,
            max_requests_per_run=1,
            max_pages_per_run=1,
            max_response_bytes=1_000_000,
            request_timeout_seconds=30,
            max_runtime_seconds=120,
            cursor_strategy=contract.cursor_strategy,
            cursor_version=1,
            collection_mode=contract.collection_mode,
            backfill_policy=CollectionBackfillPolicy.DISABLED,
            revision_policy=(
                CollectionRevisionPolicy.RECONCILE
                if candidate.source.access_method == "sec_edgar"
                else CollectionRevisionPolicy.SAFE_REPLACE
                if candidate.source.access_method == "eia"
                else CollectionRevisionPolicy.IGNORE
            ),
            rate_limit_group=f"{candidate.source.access_method}:default",
            next_due_at=now,
            health_status=CollectionTargetHealthStatus.UNKNOWN,
        )

    @staticmethod
    async def _count_unmapped(session: AsyncSession, table: str) -> int:
        if table not in {"collection_runs", "collection_cursors"}:
            raise ValueError("phase2_table_invalid")
        return int(
            await session.scalar(text(f"SELECT count(*) FROM {table} WHERE target_id IS NULL")) or 0
        )
