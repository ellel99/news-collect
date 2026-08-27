"""Read-only shadow comparison and guarded cutover/rollback evidence."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql import Executable

from market_intelligence.collection.target_configs import OperationRegistry, TargetConfigError
from market_intelligence.collection.target_repository import eligible
from market_intelligence.db.base import system_metadata
from market_intelligence.db.models import (
    AuditLog,
    CollectionCursor,
    CollectionRun,
    CollectionRunStatus,
    CollectionTarget,
    CollectionTargetStatus,
    ContentItem,
    Notification,
    RawItem,
    Source,
    SourceAccount,
)
from market_intelligence.notifications.intent import (
    POLICY_ID,
    IntentWatermark,
    _persist_cutover_watermark,
    load_cutover_watermark,
)

AUTHORITY_KEY = "collection.authority.activation.v1"
AUTHORITY_APPROVED = "reviewer-approved"


async def authority_is_approved(session: AsyncSession) -> bool:
    return (
        await session.scalar(
            select(system_metadata.c.value).where(system_metadata.c.key == AUTHORITY_KEY)
        )
        == AUTHORITY_APPROVED
    )


@dataclass(frozen=True, slots=True)
class AuthorityAudit:
    status: str
    target_count: int
    eligible_count: int
    running_runs: int
    mapping_mismatches: int
    config_mismatches: int
    cursor_mismatches: int
    provenance_mismatches: int
    notification_gaps: int
    unmapped_runs: int
    unmapped_cursors: int
    rollback_ineligible: int
    phase2_reconciled: bool
    cutover_watermark_present: bool
    authority_approved: bool
    safe_errors: tuple[str, ...]


class ControlPlaneAuditService:
    def __init__(
        self, factory: async_sessionmaker[AsyncSession], registry: OperationRegistry
    ) -> None:
        self._factory, self._registry = factory, registry

    async def shadow(self) -> AuthorityAudit:
        """Compare legacy/new authority without enqueue, external request, or write."""
        async with self._factory() as session:
            targets = tuple(
                await session.scalars(select(CollectionTarget).order_by(CollectionTarget.id))
            )
            sources = {item.id: item for item in await session.scalars(select(Source))}
            accounts = {item.id: item for item in await session.scalars(select(SourceAccount))}
            account_sources = {item.source_id for item in accounts.values()}
            mapping = config = rollback = eligible_count = 0
            for target in targets:
                source = sources.get(target.source_id)
                account = (
                    accounts.get(target.source_account_id) if target.source_account_id else None
                )
                if (
                    source is None
                    or (account is not None and account.source_id != target.source_id)
                    or (target.source_account_id is None and target.source_id in account_sources)
                ):
                    mapping += 1
                    continue
                if eligible(source, account, target):
                    eligible_count += 1
                if target.status is CollectionTargetStatus.ACTIVE and (
                    target.source_account_id is None or target.legacy_cursor_type is None
                ):
                    rollback += 1
                try:
                    contract = self._registry.resolve(
                        source.access_method,
                        target.operation_key,
                        target.operation_config_version,
                        target.provider_contract_version,
                    )
                    self._registry.validate(
                        contract,
                        target.operation_config,
                        batch_limit=target.batch_limit,
                        max_requests=target.max_requests_per_run,
                        max_pages=target.max_pages_per_run,
                    )
                    if (
                        contract.cursor_strategy is not target.cursor_strategy
                        or contract.collection_mode is not target.collection_mode
                        or contract.legacy_cursor_type != target.legacy_cursor_type
                    ):
                        raise TargetConfigError("target_operation_semantics_invalid")
                except TargetConfigError:
                    config += 1
            running = await self._count(
                session,
                select(func.count())
                .select_from(CollectionRun)
                .where(CollectionRun.status == CollectionRunStatus.RUNNING),
            )
            cursor_mismatch = await self._count(
                session,
                select(func.count())
                .select_from(CollectionCursor)
                .outerjoin(CollectionTarget, CollectionTarget.id == CollectionCursor.target_id)
                .where(
                    CollectionCursor.target_id.is_not(None),
                    CollectionCursor.source_account_id.is_distinct_from(
                        CollectionTarget.source_account_id
                    ),
                ),
            )
            provenance = await self._count(
                session,
                select(func.count())
                .select_from(RawItem)
                .join(CollectionRun, CollectionRun.id == RawItem.collection_run_id)
                .where(
                    (RawItem.source_id != CollectionRun.source_id)
                    | RawItem.source_account_id.is_distinct_from(CollectionRun.source_account_id)
                    | CollectionRun.target_id.is_(None)
                ),
            )
            unmapped_runs = await self._count(
                session,
                select(func.count())
                .select_from(CollectionRun)
                .where(CollectionRun.target_id.is_(None)),
            )
            unmapped_cursors = await self._count(
                session,
                select(func.count())
                .select_from(CollectionCursor)
                .where(CollectionCursor.target_id.is_(None)),
            )
            phase2_reconciled = bool(
                await session.scalar(
                    select(AuditLog.id)
                    .where(
                        AuditLog.action == "r1_phase2_reconciliation",
                        AuditLog.after["status"].astext == "reconciled",
                        AuditLog.after["remaining_unmapped_runs"].astext == "0",
                        AuditLog.after["remaining_unmapped_cursors"].astext == "0",
                        AuditLog.after["mismatched_target_owned_rows"].astext == "0",
                    )
                    .order_by(AuditLog.created_at.desc())
                    .limit(1)
                )
            )
            watermark = await load_cutover_watermark(session)
            authority_approved = await authority_is_approved(session)
            notification_gaps = 0
            if watermark is not None:
                notification_gaps = await self._count(
                    session,
                    select(func.count())
                    .select_from(ContentItem)
                    .where(
                        or_(
                            ContentItem.created_at > watermark.created_at,
                            and_(
                                ContentItem.created_at == watermark.created_at,
                                ContentItem.id > watermark.content_item_id,
                            ),
                        ),
                        ~ContentItem.notifications.any(Notification.policy_rule_id == POLICY_ID),
                    ),
                )
            errors = tuple(
                code
                for count, code in (
                    (mapping, "target_mapping_mismatch"),
                    (config, "target_config_mismatch"),
                    (cursor_mismatch, "cursor_identity_mismatch"),
                    (provenance, "run_raw_provenance_mismatch"),
                    (notification_gaps, "notification_intent_gap"),
                    (unmapped_runs, "collection_run_unmapped"),
                    (unmapped_cursors, "collection_cursor_unmapped"),
                    (rollback, "rollback_identity_ineligible"),
                    (not phase2_reconciled, "phase2_reconciliation_missing"),
                )
                if count
            )
            return AuthorityAudit(
                "PASS" if not errors else "BLOCKED",
                len(targets),
                eligible_count,
                running,
                mapping,
                config,
                cursor_mismatch,
                provenance,
                notification_gaps,
                unmapped_runs,
                unmapped_cursors,
                rollback,
                phase2_reconciled,
                watermark is not None,
                authority_approved,
                errors,
            )

    async def cutover_watermark_candidate(self) -> IntentWatermark | None:
        async with self._factory() as session:
            row = (
                await session.execute(
                    select(ContentItem.created_at, ContentItem.id)
                    .order_by(ContentItem.created_at.desc(), ContentItem.id.desc())
                    .limit(1)
                )
            ).one_or_none()
            return None if row is None else IntentWatermark(row.created_at, row.id)

    async def prepare_cutover_watermark(self) -> bool:
        """Persist one immutable candidate only after all non-activation gates pass."""
        report = await self.shadow()
        if report.running_runs or report.safe_errors or report.eligible_count == 0:
            raise RuntimeError("cutover_authority_audit_blocked")
        candidate = await self.cutover_watermark_candidate()
        if candidate is None:
            raise RuntimeError("cutover_watermark_candidate_missing")
        async with self._factory.begin() as session:
            existing = await load_cutover_watermark(session)
            if existing is not None:
                if existing != candidate:
                    raise RuntimeError("cutover_watermark_immutable")
                return False
            return await _persist_cutover_watermark(session, candidate)

    async def rollback_eligible(self) -> bool:
        report = await self.shadow()
        return (
            report.running_runs == 0
            and report.mapping_mismatches == 0
            and report.config_mismatches == 0
            and report.cursor_mismatches == 0
            and report.provenance_mismatches == 0
            and report.unmapped_runs == 0
            and report.unmapped_cursors == 0
            and report.phase2_reconciled
            and report.rollback_ineligible == 0
        )

    @staticmethod
    async def _count(session: AsyncSession, statement: Executable) -> int:
        return int(await session.scalar(statement) or 0)
