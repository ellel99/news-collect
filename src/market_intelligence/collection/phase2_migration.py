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
    """Value-free Phase 2 failure that rolls back the whole transaction."""


@dataclass(frozen=True, slots=True)
class Phase2Report:
    source_count: int
    account_count: int
    candidate_count: int
    created_count: int
    existing_count: int
    blocked_count: int
    expected_runs: int
    runs_backfilled: int
    remaining_unmapped_runs: int
    expected_cursors: int
    cursors_backfilled: int
    remaining_unmapped_cursors: int
    mismatched_target_owned_rows: int


@dataclass(frozen=True, slots=True)
class _Candidate:
    source: Source
    account: SourceAccount | None
    contract: OperationContract | None
    config: dict[str, Any]
    valid: bool
    legacy_cursor_type: str | None


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
                rows: list[SourceAccount | None] = list(by_source.get(source.id, [])) or [None]
                candidates.extend(self._candidate(source, account) for account in rows)

            identity_counts: dict[tuple[Any, str], int] = {}
            for candidate in candidates:
                if candidate.account is not None and candidate.legacy_cursor_type is not None:
                    key = (candidate.account.id, candidate.legacy_cursor_type)
                    identity_counts[key] = identity_counts.get(key, 0) + 1

            created = existing = blocked = 0
            for candidate in candidates:
                target_key = self._target_key(candidate.source, candidate.account)
                if candidate.account is not None and candidate.legacy_cursor_type is not None:
                    key = (candidate.account.id, candidate.legacy_cursor_type)
                    owner = await session.scalar(
                        select(CollectionTarget).where(
                            CollectionTarget.source_account_id == candidate.account.id,
                            CollectionTarget.legacy_cursor_type == candidate.legacy_cursor_type,
                        )
                    )
                    if identity_counts[key] != 1 or (
                        owner is not None and owner.target_key != target_key
                    ):
                        candidate = _Candidate(
                            candidate.source,
                            candidate.account,
                            candidate.contract,
                            candidate.config,
                            False,
                            None,
                        )
                expected = self._build_target(candidate, target_key)
                target = await session.scalar(
                    select(CollectionTarget).where(CollectionTarget.target_key == target_key)
                )
                if target is not None:
                    self._assert_exact_target(target, expected)
                    existing += 1
                    blocked += int(target.status is CollectionTargetStatus.BLOCKED)
                    continue
                session.add(expected)
                await session.flush()
                created += 1
                blocked += int(expected.status is CollectionTargetStatus.BLOCKED)
                session.add(
                    AuditLog(
                        actor_type="system",
                        actor_id=None,
                        action="r1_phase2_target_created",
                        target_type="collection_target",
                        target_id=expected.id,
                        before=None,
                        after={"status": expected.status.value, "mapping_valid": candidate.valid},
                    )
                )

            expected_runs = await self._count_unmapped(session, "collection_runs")
            expected_cursors = await self._count_unmapped(session, "collection_cursors")
            await self._assert_exact_backfill_candidates(session)
            await session.execute(text("SELECT set_config('r1.phase2_backfill','on',true)"))
            run_result = await session.execute(
                text("""
                UPDATE collection_runs r SET target_id=t.id FROM collection_targets t
                WHERE r.target_id IS NULL AND r.source_id=t.source_id
                  AND r.source_account_id IS NOT DISTINCT FROM t.source_account_id
            """)
            )
            cursor_result = await session.execute(
                text("""
                UPDATE collection_cursors c SET target_id=t.id FROM collection_targets t
                WHERE c.target_id IS NULL AND c.source_account_id=t.source_account_id
                  AND c.cursor_type=t.legacy_cursor_type
            """)
            )
            runs = int(getattr(run_result, "rowcount", 0))
            cursors = int(getattr(cursor_result, "rowcount", 0))
            remaining_runs = await self._count_unmapped(session, "collection_runs")
            remaining_cursors = await self._count_unmapped(session, "collection_cursors")
            mismatched = await self._target_owned_mismatch_count(session)
            if (
                runs != expected_runs
                or cursors != expected_cursors
                or remaining_runs
                or remaining_cursors
                or mismatched
            ):
                raise Phase2MigrationError("phase2_reconciliation_count_mismatch")
            session.add(
                AuditLog(
                    actor_type="system",
                    actor_id=None,
                    action="r1_phase2_reconciliation",
                    target_type="collection_target",
                    target_id=None,
                    before={"expected_runs": expected_runs, "expected_cursors": expected_cursors},
                    after={
                        "created_count": created,
                        "existing_count": existing,
                        "blocked_count": blocked,
                        "runs_backfilled": runs,
                        "remaining_unmapped_runs": remaining_runs,
                        "cursors_backfilled": cursors,
                        "remaining_unmapped_cursors": remaining_cursors,
                        "mismatched_target_owned_rows": mismatched,
                        "status": "reconciled",
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
                expected_runs,
                runs,
                remaining_runs,
                expected_cursors,
                cursors,
                remaining_cursors,
                mismatched,
            )

    async def _preflight(self, session: AsyncSession) -> None:
        checks = {
            "phase2_raw_run_provenance_mismatch": """
                SELECT count(*) FROM raw_items i
                JOIN collection_runs r ON r.id=i.collection_run_id
                WHERE i.source_id IS DISTINCT FROM r.source_id
                   OR i.source_account_id IS DISTINCT FROM r.source_account_id
            """,
            "phase2_run_source_account_mismatch": """
                SELECT count(*) FROM collection_runs r
                LEFT JOIN sources s ON s.id=r.source_id
                LEFT JOIN source_accounts a ON a.id=r.source_account_id
                WHERE s.id IS NULL OR (r.source_account_id IS NOT NULL AND
                  (a.id IS NULL OR a.source_id IS DISTINCT FROM r.source_id))
            """,
            "phase2_cursor_account_missing": """
                SELECT count(*) FROM collection_cursors c
                LEFT JOIN source_accounts a ON a.id=c.source_account_id WHERE a.id IS NULL
            """,
            "phase2_content_provenance_mismatch": """
                SELECT count(*) FROM content_items c LEFT JOIN raw_items r ON r.id=c.raw_item_id
                WHERE r.id IS NULL OR c.source_id IS DISTINCT FROM r.source_id
                   OR c.source_account_id IS DISTINCT FROM r.source_account_id
            """,
            "phase2_evidence_provenance_mismatch": """
                SELECT count(*) FROM evidence_items e LEFT JOIN raw_items r ON r.id=e.raw_item_id
                LEFT JOIN content_items c ON c.id=e.content_item_id
                WHERE r.id IS NULL OR e.source_id IS DISTINCT FROM r.source_id
                   OR e.source_account_id IS DISTINCT FROM r.source_account_id
                   OR (e.content_item_id IS NOT NULL AND
                     (c.raw_item_id IS DISTINCT FROM e.raw_item_id
                      OR c.source_id IS DISTINCT FROM e.source_id
                      OR c.source_account_id IS DISTINCT FROM e.source_account_id))
            """,
        }
        for code, statement in checks.items():
            if int(await session.scalar(text(statement)) or 0):
                raise Phase2MigrationError(code)
        if await self._target_owned_mismatch_count(session):
            raise Phase2MigrationError("phase2_target_owned_provenance_mismatch")

    async def _assert_exact_backfill_candidates(self, session: AsyncSession) -> None:
        run_bad = int(
            await session.scalar(
                text(
                    """SELECT count(*) FROM collection_runs r
                    WHERE r.target_id IS NULL AND
                      (SELECT count(*) FROM collection_targets t
                       WHERE t.source_id=r.source_id AND
                         t.source_account_id IS NOT DISTINCT FROM r.source_account_id) <> 1"""
                )
            )
            or 0
        )
        cursor_bad = int(
            await session.scalar(
                text(
                    """SELECT count(*) FROM collection_cursors c
                    WHERE c.target_id IS NULL AND
                      (SELECT count(*) FROM collection_targets t
                       WHERE t.source_account_id=c.source_account_id AND
                         t.legacy_cursor_type=c.cursor_type) <> 1"""
                )
            )
            or 0
        )
        if run_bad or cursor_bad:
            raise Phase2MigrationError("phase2_backfill_candidate_not_unique")

    async def _target_owned_mismatch_count(self, session: AsyncSession) -> int:
        return int(
            await session.scalar(
                text(
                    """SELECT
                    (SELECT count(*) FROM collection_runs r
                     JOIN collection_targets t ON t.id=r.target_id
                     WHERE r.source_id IS DISTINCT FROM t.source_id OR
                       r.source_account_id IS DISTINCT FROM t.source_account_id)
                    + (SELECT count(*) FROM collection_cursors c
                       JOIN collection_targets t ON t.id=c.target_id
                       WHERE c.source_account_id IS DISTINCT FROM t.source_account_id)"""
                )
            )
            or 0
        )

    def _candidate(self, source: Source, account: SourceAccount | None) -> _Candidate:
        mapping = {
            "fake": "fake_sequence",
            "marketaux": "news_all",
            "finnhub": "quote",
            "eia": "electricity_retail_sales",
            "sec_edgar": "submissions_recent",
        }
        operation = mapping.get(source.access_method)
        if operation is None or (account is None and source.access_method != "fake"):
            return _Candidate(source, account, None, {}, False, None)
        config_source = dict(account.collection_options) if account else {}
        try:
            contract = self._registry.resolve(source.access_method, operation, 1, 1)
            config = dict(
                self._registry.validate(
                    contract,
                    config_source,
                    batch_limit=contract.batch_ceiling,
                    max_requests=1,
                    max_pages=1,
                )
            )
        except TargetConfigError:
            return _Candidate(source, account, None, {}, False, None)
        return _Candidate(
            source,
            account,
            contract,
            config,
            True,
            contract.legacy_cursor_type if account else None,
        )

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
            legacy_cursor_type=candidate.legacy_cursor_type,
            operation_config_version=contract.operation_config_version,
            provider_contract_version=contract.provider_contract_version,
            operation_config=candidate.config,
            status=CollectionTargetStatus.PAUSED
            if candidate.valid
            else CollectionTargetStatus.BLOCKED,
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
            revision_policy=CollectionRevisionPolicy.RECONCILE
            if candidate.source.access_method == "sec_edgar"
            else CollectionRevisionPolicy.SAFE_REPLACE
            if candidate.source.access_method == "eia"
            else CollectionRevisionPolicy.IGNORE,
            rate_limit_group=(
                "fake:test"
                if candidate.source.access_method == "fake"
                else f"{candidate.source.access_method}:default"
            ),
            next_due_at=now,
            health_status=CollectionTargetHealthStatus.UNKNOWN,
        )

    @staticmethod
    def _assert_exact_target(actual: CollectionTarget, expected: CollectionTarget) -> None:
        fields = (
            "target_key",
            "source_id",
            "source_account_id",
            "operation_key",
            "legacy_cursor_type",
            "operation_config_version",
            "provider_contract_version",
            "operation_config",
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
            "health_status",
            "next_retry_at",
            "last_attempt_at",
            "last_success_at",
            "last_error_code",
        )
        if (
            actual.config_revision != 1
            or actual.priority != 100
            or actual.consecutive_failures != 0
            or any(getattr(actual, field) != getattr(expected, field) for field in fields)
        ):
            raise Phase2MigrationError("phase2_existing_target_mismatch")

    @staticmethod
    async def _count_unmapped(session: AsyncSession, table: str) -> int:
        if table not in {"collection_runs", "collection_cursors"}:
            raise ValueError("phase2_table_invalid")
        return int(
            await session.scalar(text(f"SELECT count(*) FROM {table} WHERE target_id IS NULL")) or 0
        )
