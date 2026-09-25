"""Bounded, retryable and stale-safe M2-C bundle reconciliation worker."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import exists, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from market_intelligence.db.base import system_metadata
from market_intelligence.db.models import (
    EventCandidate,
    EventCandidateEvidence,
    EventEvidenceBundleJob,
    EventEvidenceBundleJobStatus,
)
from market_intelligence.event_evidence.contracts import BundleConflict, BundleWorkerReport
from market_intelligence.event_evidence.service import EventEvidenceBundleService

_DISCOVERY_KEY = "event_evidence_bundle_discovery_cursor"


class EventEvidenceBundleWorker:
    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        *,
        max_attempts: int = 3,
        stale_after: timedelta = timedelta(minutes=10),
        retry_delay: timedelta = timedelta(minutes=1),
    ) -> None:
        if not 1 <= max_attempts <= 10:
            raise ValueError("event_bundle_max_attempts_invalid")
        self._factory = factory
        self._service = EventEvidenceBundleService(factory)
        self._max_attempts = max_attempts
        self._stale_after = stale_after
        self._retry_delay = retry_delay

    async def process_batch(self, *, limit: int = 100) -> BundleWorkerReport:
        if not 1 <= limit <= 500:
            raise ValueError("event_bundle_batch_limit_invalid")
        now = datetime.now(UTC)
        recovered = await self._recover_stale(now, limit)
        discovered = await self._discover(limit)
        claimed = await self._claim(now, limit)
        counts = {"ready": 0, "partial": 0, "unchanged": 0, "blocked": 0, "retry": 0}
        for identity, claim_token in claimed:
            try:
                result = await self._service.build(identity)
                outcome = result.status
                await self._complete(identity, claim_token, result.status, result.bundle_id, now)
            except BundleConflict as exc:
                outcome = "blocked"
                await self._block(identity, claim_token, str(exc), now)
            except (DBAPIError, IntegrityError):
                outcome = await self._retry(
                    identity, claim_token, "event_bundle_database_conflict", now
                )
            except Exception:
                outcome = await self._retry(identity, claim_token, "event_bundle_unexpected", now)
            counts[outcome] += 1
        return BundleWorkerReport(
            discovered,
            len(claimed),
            counts["ready"],
            counts["partial"],
            counts["unchanged"],
            counts["blocked"],
            counts["retry"],
            recovered,
        )

    async def _discover(self, limit: int) -> int:
        async with self._factory.begin() as session:
            await session.execute(
                insert(system_metadata)
                .values(key=_DISCOVERY_KEY, value="")
                .on_conflict_do_nothing(index_elements=[system_metadata.c.key])
            )
            cursor_value = await session.scalar(
                select(system_metadata.c.value)
                .where(system_metadata.c.key == _DISCOVERY_KEY)
                .with_for_update()
            )
            cursor = uuid.UUID(cursor_value) if cursor_value else None
            eligible = or_(
                EventEvidenceBundleJob.event_candidate_id.is_(None),
                EventEvidenceBundleJob.status.in_(
                    (
                        EventEvidenceBundleJobStatus.READY,
                        EventEvidenceBundleJobStatus.PARTIAL,
                    )
                ),
            )
            base = (
                select(EventCandidate.id)
                .outerjoin(
                    EventEvidenceBundleJob,
                    EventEvidenceBundleJob.event_candidate_id == EventCandidate.id,
                )
                .where(
                    exists().where(
                        EventCandidateEvidence.event_candidate_id == EventCandidate.id,
                        EventCandidateEvidence.active.is_(True),
                    ),
                    eligible,
                )
                .order_by(EventCandidate.id)
                .limit(limit)
            )
            if cursor is not None:
                base = base.where(EventCandidate.id > cursor)
            candidates = tuple(await session.scalars(base))
            if not candidates and cursor is not None:
                candidates = tuple(
                    await session.scalars(
                        select(EventCandidate.id)
                        .outerjoin(
                            EventEvidenceBundleJob,
                            EventEvidenceBundleJob.event_candidate_id == EventCandidate.id,
                        )
                        .where(
                            exists().where(
                                EventCandidateEvidence.event_candidate_id == EventCandidate.id,
                                EventCandidateEvidence.active.is_(True),
                            ),
                            eligible,
                        )
                        .order_by(EventCandidate.id)
                        .limit(limit)
                    )
                )
            if not candidates:
                return 0
            statement = insert(EventEvidenceBundleJob).values(
                [
                    {
                        "event_candidate_id": identity,
                        "status": EventEvidenceBundleJobStatus.PENDING.value,
                    }
                    for identity in candidates
                ]
            )
            statement = statement.on_conflict_do_update(
                index_elements=["event_candidate_id"],
                set_={
                    "status": EventEvidenceBundleJobStatus.PENDING.value,
                    "updated_at": datetime.now(UTC),
                },
                where=EventEvidenceBundleJob.status.in_(
                    (
                        EventEvidenceBundleJobStatus.READY,
                        EventEvidenceBundleJobStatus.PARTIAL,
                    )
                ),
            )
            await session.execute(statement)
            await session.execute(
                update(system_metadata)
                .where(system_metadata.c.key == _DISCOVERY_KEY)
                .values(value=str(candidates[-1]), updated_at=datetime.now(UTC))
            )
            return len(candidates)

    async def _recover_stale(self, now: datetime, limit: int) -> int:
        async with self._factory.begin() as session:
            rows = tuple(
                await session.scalars(
                    select(EventEvidenceBundleJob)
                    .where(
                        EventEvidenceBundleJob.status == EventEvidenceBundleJobStatus.PROCESSING,
                        EventEvidenceBundleJob.processing_started_at < now - self._stale_after,
                    )
                    .order_by(EventEvidenceBundleJob.processing_started_at)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            )
            for row in rows:
                if row.attempt_count >= self._max_attempts:
                    row.status = EventEvidenceBundleJobStatus.BLOCKED
                    row.safe_error_code = "event_bundle_retry_exhausted"
                    row.next_retry_at = None
                else:
                    row.status = EventEvidenceBundleJobStatus.RETRY
                    row.safe_error_code = "event_bundle_stale"
                    row.next_retry_at = now
                row.processing_started_at = None
                row.claim_token = None
                row.updated_at = now
            return len(rows)

    async def _claim(self, now: datetime, limit: int) -> tuple[tuple[uuid.UUID, uuid.UUID], ...]:
        async with self._factory.begin() as session:
            rows = tuple(
                await session.scalars(
                    select(EventEvidenceBundleJob)
                    .where(
                        EventEvidenceBundleJob.status.in_(
                            (
                                EventEvidenceBundleJobStatus.PENDING,
                                EventEvidenceBundleJobStatus.RETRY,
                            )
                        ),
                        or_(
                            EventEvidenceBundleJob.next_retry_at.is_(None),
                            EventEvidenceBundleJob.next_retry_at <= now,
                        ),
                    )
                    .order_by(
                        EventEvidenceBundleJob.updated_at, EventEvidenceBundleJob.event_candidate_id
                    )
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            )
            claims: list[tuple[uuid.UUID, uuid.UUID]] = []
            for row in rows:
                token = uuid.uuid4()
                row.status = EventEvidenceBundleJobStatus.PROCESSING
                row.attempt_count += 1
                row.processing_started_at = now
                row.claim_token = token
                row.next_retry_at = None
                row.safe_error_code = None
                row.updated_at = now
                claims.append((row.event_candidate_id, token))
            return tuple(claims)

    async def _complete(
        self,
        identity: uuid.UUID,
        claim_token: uuid.UUID,
        status: str,
        bundle_id: uuid.UUID | None,
        now: datetime,
    ) -> None:
        final = (
            EventEvidenceBundleJobStatus.PARTIAL
            if status == "partial"
            else EventEvidenceBundleJobStatus.READY
        )
        async with self._factory.begin() as session:
            row = await session.get(EventEvidenceBundleJob, identity, with_for_update=True)
            if (
                row is None
                or row.status is not EventEvidenceBundleJobStatus.PROCESSING
                or row.claim_token != claim_token
            ):
                return
            if status == "unchanged" and bundle_id is not None:
                from market_intelligence.db.models import EventEvidenceBundle

                bundle = await session.get(EventEvidenceBundle, bundle_id)
                if bundle is not None and bundle.status.value == "partial":
                    final = EventEvidenceBundleJobStatus.PARTIAL
            row.status = final
            row.latest_bundle_id = bundle_id
            row.attempt_count = 0
            row.processing_started_at = None
            row.claim_token = None
            row.next_retry_at = None
            row.safe_error_code = None
            row.updated_at = now

    async def _block(
        self, identity: uuid.UUID, claim_token: uuid.UUID, code: str, now: datetime
    ) -> None:
        await self._set_failure(
            identity,
            claim_token,
            EventEvidenceBundleJobStatus.BLOCKED,
            code,
            None,
            now,
        )

    async def _retry(
        self, identity: uuid.UUID, claim_token: uuid.UUID, code: str, now: datetime
    ) -> str:
        async with self._factory.begin() as session:
            row = await session.get(EventEvidenceBundleJob, identity, with_for_update=True)
            if (
                row is None
                or row.status is not EventEvidenceBundleJobStatus.PROCESSING
                or row.claim_token != claim_token
            ):
                return "blocked"
            exhausted = row.attempt_count >= self._max_attempts
            row.status = (
                EventEvidenceBundleJobStatus.BLOCKED
                if exhausted
                else EventEvidenceBundleJobStatus.RETRY
            )
            row.safe_error_code = "event_bundle_retry_exhausted" if exhausted else code
            row.processing_started_at = None
            row.claim_token = None
            row.next_retry_at = None if exhausted else now + self._retry_delay
            row.updated_at = now
            return "blocked" if exhausted else "retry"

    async def _set_failure(
        self,
        identity: uuid.UUID,
        claim_token: uuid.UUID,
        status: EventEvidenceBundleJobStatus,
        code: str,
        retry_at: datetime | None,
        now: datetime,
    ) -> None:
        async with self._factory.begin() as session:
            await session.execute(
                update(EventEvidenceBundleJob)
                .where(
                    EventEvidenceBundleJob.event_candidate_id == identity,
                    EventEvidenceBundleJob.status == EventEvidenceBundleJobStatus.PROCESSING,
                    EventEvidenceBundleJob.claim_token == claim_token,
                )
                .values(
                    status=status,
                    safe_error_code=code,
                    processing_started_at=None,
                    claim_token=None,
                    next_retry_at=retry_at,
                    updated_at=now,
                )
            )
