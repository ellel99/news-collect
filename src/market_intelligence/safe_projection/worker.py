"""Bounded, restart-safe validation worker for durable factual projections."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from market_intelligence.db.models import (
    SafeFactProjection,
    SafeProjectionProcessingStatus,
    SafeProjectionQualityStatus,
)
from market_intelligence.safe_projection.contracts import (
    ProjectionContractError,
    canonical_projection_hash,
    normalize_and_classify_factual_payload,
)


@dataclass(frozen=True, slots=True)
class ProjectionValidationReport:
    claimed: int
    ready: int
    blocked: int
    recovered: int


class SafeFactProjectionWorker:
    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        *,
        max_attempts: int = 3,
        stale_after: timedelta = timedelta(minutes=10),
    ) -> None:
        self._factory = factory
        self._max_attempts = max_attempts
        self._stale_after = stale_after

    async def process_batch(self, *, limit: int = 100) -> ProjectionValidationReport:
        if not 1 <= limit <= 500:
            raise ValueError("projection_worker_limit_invalid")
        now = datetime.now(UTC)
        recovered = await self._recover_stale(now, limit)
        claimed = await self._claim(limit, now)
        ready = blocked = 0
        for identity in claimed:
            if await self._validate_one(identity, now):
                ready += 1
            else:
                blocked += 1
        return ProjectionValidationReport(len(claimed), ready, blocked, recovered)

    async def _recover_stale(self, now: datetime, limit: int) -> int:
        cutoff = now - self._stale_after
        async with self._factory.begin() as session:
            rows = tuple(
                await session.scalars(
                    select(SafeFactProjection)
                    .where(
                        SafeFactProjection.processing_status
                        == SafeProjectionProcessingStatus.VALIDATING,
                        SafeFactProjection.updated_at < cutoff,
                    )
                    .order_by(SafeFactProjection.updated_at, SafeFactProjection.id)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            )
            for row in rows:
                if row.attempt_count >= self._max_attempts:
                    row.processing_status = SafeProjectionProcessingStatus.BLOCKED
                    row.quality_status = SafeProjectionQualityStatus.BLOCKED
                    row.safe_error_code = "projection_retry_exhausted"
                    row.processed_at = now
                    row.next_retry_at = None
                else:
                    row.processing_status = SafeProjectionProcessingStatus.RETRY
                    row.safe_error_code = "projection_validation_stale"
                    row.next_retry_at = now
                row.updated_at = now
            return len(rows)

    async def _claim(self, limit: int, now: datetime) -> tuple[UUID, ...]:
        async with self._factory.begin() as session:
            rows = tuple(
                await session.scalars(
                    select(SafeFactProjection)
                    .where(
                        SafeFactProjection.processing_status.in_(
                            (
                                SafeProjectionProcessingStatus.PENDING,
                                SafeProjectionProcessingStatus.RETRY,
                            )
                        ),
                        or_(
                            SafeFactProjection.next_retry_at.is_(None),
                            SafeFactProjection.next_retry_at <= now,
                        ),
                    )
                    .order_by(SafeFactProjection.created_at, SafeFactProjection.id)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            )
            for row in rows:
                row.processing_status = SafeProjectionProcessingStatus.VALIDATING
                row.attempt_count += 1
                row.updated_at = now
                row.safe_error_code = None
                row.next_retry_at = None
            return tuple(row.id for row in rows)

    async def _validate_one(self, identity: UUID, now: datetime) -> bool:
        async with self._factory.begin() as session:
            row = await session.get(SafeFactProjection, identity, with_for_update=True)
            if (
                row is None
                or row.processing_status is not SafeProjectionProcessingStatus.VALIDATING
            ):
                return False
            try:
                normalized, quality = normalize_and_classify_factual_payload(
                    row.provider,
                    row.operation_key,
                    row.projection_schema_version,
                    row.factual_payload,
                )
                if canonical_projection_hash(normalized) != row.projection_hash:
                    raise ProjectionContractError("projection_hash_mismatch")
            except ProjectionContractError as exc:
                row.processing_status = SafeProjectionProcessingStatus.BLOCKED
                row.quality_status = SafeProjectionQualityStatus.BLOCKED
                row.safe_error_code = str(exc)
                row.processed_at = now
                row.updated_at = now
                return False
            row.factual_payload = normalized
            row.quality_status = SafeProjectionQualityStatus(quality)
            row.processing_status = SafeProjectionProcessingStatus.READY
            row.safe_error_code = None
            row.processed_at = now
            row.updated_at = now
            return True
