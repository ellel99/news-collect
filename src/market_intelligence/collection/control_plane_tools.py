"""Read-only shadow comparison and guarded cutover/rollback evidence."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql import Executable

from market_intelligence.collection.target_configs import OperationRegistry, TargetConfigError
from market_intelligence.collection.target_repository import eligible
from market_intelligence.db.models import (
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
    load_cutover_watermark,
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
    content_gaps: int
    evidence_gaps: int
    notification_gaps: int
    rollback_ineligible: int
    cutover_watermark_present: bool
    safe_errors: tuple[str, ...]


class ControlPlaneAuditService:
    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        registry: OperationRegistry,
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
                    CollectionCursor.target_id.is_(None)
                    | CollectionCursor.source_account_id.is_distinct_from(
                        CollectionTarget.source_account_id
                    )
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
            content_gaps = await self._count(
                session,
                select(func.count())
                .select_from(RawItem)
                .join(CollectionRun, CollectionRun.id == RawItem.collection_run_id)
                .where(CollectionRun.target_id.is_not(None), ~RawItem.content_item.has()),
            )
            evidence_gaps = await self._count(
                session,
                select(func.count())
                .select_from(RawItem)
                .join(CollectionRun, CollectionRun.id == RawItem.collection_run_id)
                .where(CollectionRun.target_id.is_not(None), ~RawItem.evidence_items.any()),
            )
            watermark = await load_cutover_watermark(session)
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
                    (content_gaps, "content_completeness_gap"),
                    (evidence_gaps, "evidence_completeness_gap"),
                    (notification_gaps, "notification_intent_gap"),
                    (rollback, "rollback_identity_ineligible"),
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
                content_gaps,
                evidence_gaps,
                notification_gaps,
                rollback,
                watermark is not None,
                errors,
            )

    async def cutover_watermark_candidate(self) -> IntentWatermark | None:
        """Read the stable newest Content tuple; never persists or activates it."""
        async with self._factory() as session:
            row = (
                await session.execute(
                    select(ContentItem.created_at, ContentItem.id)
                    .order_by(ContentItem.created_at.desc(), ContentItem.id.desc())
                    .limit(1)
                )
            ).one_or_none()
            return None if row is None else IntentWatermark(row.created_at, row.id)

    async def rollback_eligible(self) -> bool:
        report = await self.shadow()
        return (
            report.running_runs == 0
            and report.mapping_mismatches == 0
            and report.cursor_mismatches == 0
            and report.provenance_mismatches == 0
            and report.rollback_ineligible == 0
        )

    @staticmethod
    async def _count(session: AsyncSession, statement: Executable) -> int:
        return int(await session.scalar(statement) or 0)
