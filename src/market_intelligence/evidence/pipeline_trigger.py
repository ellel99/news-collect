"""Explicit trigger from a persisted RawItem and safe projection to evidence."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum

from market_intelligence.evidence.orchestration import (
    EvidencePipelineOutcome,
    EvidencePipelineRequest,
    EvidencePipelineService,
)
from market_intelligence.evidence.projection_store import (
    EvidenceProjectionStore,
    RawItemEvidenceProjection,
    RawItemProjectionReader,
    SafeRawItemProjectionSource,
)


class EvidenceTriggerStatus(StrEnum):
    PROCESSED = "processed"
    INVALID = "invalid"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class EvidenceTriggerError:
    code: str
    safe_message: str


@dataclass(frozen=True, slots=True)
class EvidenceTriggerOutcome:
    status: EvidenceTriggerStatus
    raw_item_id: uuid.UUID
    pipeline_outcome: EvidencePipelineOutcome | None
    safe_errors: tuple[EvidenceTriggerError, ...] = ()


class RawItemEvidencePipelineTrigger:
    def __init__(
        self,
        raw_items: RawItemProjectionReader,
        projections: EvidenceProjectionStore,
        pipeline: EvidencePipelineService,
    ) -> None:
        self._raw_items = raw_items
        self._projections = projections
        self._pipeline = pipeline

    async def trigger(self, raw_item_id: uuid.UUID) -> EvidenceTriggerOutcome:
        raw_item = await self._raw_items.get(raw_item_id)
        if raw_item is None:
            return _failed(raw_item_id, EvidenceTriggerStatus.INVALID, "raw_item_not_found")
        projection = self._projections.get(raw_item_id)
        if projection is None:
            return _failed(raw_item_id, EvidenceTriggerStatus.SKIPPED, "projection_not_found")
        if not _matches(raw_item, projection):
            return _failed(raw_item_id, EvidenceTriggerStatus.INVALID, "projection_mismatch")

        try:
            pipeline_outcome = await self._pipeline.process(
                EvidencePipelineRequest(
                    raw_item_id=raw_item.raw_item_id,
                    source_id=raw_item.source_id,
                    source_account_id=raw_item.source_account_id,
                    provider=projection.provider,
                    sanitized_projection=projection.sanitized_projection,
                    observed_at=projection.observed_at,
                    correlation_id=projection.correlation_id,
                )
            )
        except Exception:
            return _failed(raw_item_id, EvidenceTriggerStatus.FAILED, "pipeline_failed")
        return EvidenceTriggerOutcome(
            status=EvidenceTriggerStatus.PROCESSED,
            raw_item_id=raw_item_id,
            pipeline_outcome=pipeline_outcome,
        )


def _matches(
    raw_item: SafeRawItemProjectionSource,
    projection: RawItemEvidenceProjection,
) -> bool:
    values = projection.sanitized_projection
    return (
        projection.raw_item_id == raw_item.raw_item_id
        and projection.source_id == raw_item.source_id
        and projection.source_account_id == raw_item.source_account_id
        and projection.provider in {"marketaux", "finnhub", "eia", "sec_edgar"}
        and values.get("provider_item_id") == raw_item.external_id
        and values.get("payload_hash") == raw_item.payload_hash
        and values.get("payload_reference") == raw_item.payload_location
    )


def _failed(
    raw_item_id: uuid.UUID,
    status: EvidenceTriggerStatus,
    code: str,
) -> EvidenceTriggerOutcome:
    return EvidenceTriggerOutcome(
        status=status,
        raw_item_id=raw_item_id,
        pipeline_outcome=None,
        safe_errors=(EvidenceTriggerError(code=code, safe_message=code),),
    )
