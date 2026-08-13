"""Explicit mock collection-to-evidence orchestration with no provider IO."""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from market_intelligence.collection.contracts import CollectionTarget
from market_intelligence.collection.errors import ClassifiedCollectionError
from market_intelligence.collection.runner import CollectionRunner
from market_intelligence.db.models import RawItem
from market_intelligence.evidence.orchestration import EvidencePipelineService
from market_intelligence.evidence.pipeline_trigger import (
    EvidenceTriggerOutcome,
    RawItemEvidencePipelineTrigger,
)
from market_intelligence.evidence.projection_store import (
    InMemoryEvidenceProjectionStore,
    RawItemEvidenceProjection,
    SafeRawItemProjectionSource,
    SqlAlchemyRawItemProjectionReader,
)
from market_intelligence.evidence.write_path import EvidenceWriteService
from market_intelligence.providers.contracts import ProviderFetchResult

_EVIDENCE_METADATA_FIELDS = {
    "marketaux": frozenset(
        {
            "provider_item_id",
            "published_at",
            "field_names",
            "has_title",
            "has_description",
            "has_snippet",
            "has_source_url",
            "safe_title",
            "safe_summary",
            "public_url",
        }
    ),
    "finnhub": frozenset(
        {
            "provider_item_id",
            "published_at",
            "field_names",
            "symbol",
            "numeric_field_count",
            "current",
            "previous_close",
            "absolute_change",
            "change_percent",
            "high",
            "low",
            "open",
        }
    ),
    "eia": frozenset(
        {
            "provider_item_id",
            "published_at",
            "field_names",
            "geography",
            "sector",
            "has_numeric_value",
            "dataset",
            "period",
            "metric",
            "value",
            "unit",
        }
    ),
    "sec_edgar": frozenset(
        {
            "provider_item_id",
            "published_at",
            "field_names",
            "ticker",
            "form",
            "has_primary_document",
        }
    ),
}
_DISPLAY_METADATA_FIELDS = frozenset(
    {
        "provider_item_id",
        "published_at",
        "display_title",
        "display_url",
        "display_summary",
        "structured_facts",
    }
)
_SECRET_VALUE = re.compile(
    r"(?i)(api[_-]?key|api[_-]?token|authorization|x-finnhub-token|token|secret|password)="
)
_PUBLIC_URL = re.compile(r"(?i)^https?://([^/?#]+)(?:[/?#].*)?$")


class EndToEndStatus(StrEnum):
    PROCESSED = "processed"
    COLLECTION_FAILED = "collection_failed"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class EndToEndError:
    code: str
    safe_message: str


class EvidenceEventEnqueuer(Protocol):
    def enqueue(self, evidence_item_id: uuid.UUID) -> None: ...


@dataclass(frozen=True, slots=True)
class EndToEndOutcome:
    status: EndToEndStatus
    collection_run_id: uuid.UUID | None
    raw_item_count: int
    trigger_outcomes: tuple[EvidenceTriggerOutcome, ...] = ()
    safe_errors: tuple[EndToEndError, ...] = ()
    retry_delay: float | None = None


@dataclass(frozen=True, slots=True)
class _PendingProjection:
    provider: str
    metadata: Mapping[str, object]
    display: Mapping[str, object]
    observed_at: datetime


@dataclass(slots=True)
class InMemoryProviderProjectionSidecar:
    """Retain only sanitized metadata aligned to provider raw envelopes."""

    _pending: dict[tuple[str | None, str | None, str | None], _PendingProjection] = field(
        default_factory=dict
    )

    def observe(self, result: ProviderFetchResult) -> None:
        allowed_fields = _EVIDENCE_METADATA_FIELDS.get(result.provider)
        if allowed_fields is None:
            raise ValueError("provider_unsupported")
        if result.display_projections and len(result.display_projections) != len(result.raw_items):
            raise ValueError("provider_display_projection_missing")
        displays = result.display_projections or tuple({} for _ in result.raw_items)
        for item, metadata, display in zip(
            result.raw_items, result.sanitized_metadata, displays, strict=True
        ):
            if set(metadata) != allowed_fields:
                raise ValueError("provider_evidence_projection_invalid")
            if display and not _valid_display_projection(display):
                raise ValueError("provider_display_projection_invalid")
            key = (item.external_id, item.payload_hash, item.payload_location)
            self._pending[key] = _PendingProjection(
                result.provider, dict(metadata), dict(display), item.fetched_at
            )

    def bind(self, raw_item: SafeRawItemProjectionSource) -> RawItemEvidenceProjection | None:
        key = (raw_item.external_id, raw_item.payload_hash, raw_item.payload_location)
        item = self._pending.get(key)
        if item is None:
            return None
        projection = dict(item.metadata)
        projection["payload_hash"] = raw_item.payload_hash
        projection["payload_reference"] = raw_item.payload_location
        return RawItemEvidenceProjection(
            raw_item_id=raw_item.raw_item_id,
            source_id=raw_item.source_id,
            source_account_id=raw_item.source_account_id,
            provider=item.provider,
            sanitized_projection=projection,
            observed_at=item.observed_at,
            correlation_id=f"collection-run:{raw_item.raw_item_id}",
        )

    def display_metadata(
        self, raw_item: SafeRawItemProjectionSource
    ) -> Mapping[str, object] | None:
        """Return the adapter-sanitized display projection for same-run persistence."""

        key = (raw_item.external_id, raw_item.payload_hash, raw_item.payload_location)
        item = self._pending.get(key)
        return None if item is None else dict(item.display)


def _valid_display_projection(value: Mapping[str, object]) -> bool:
    if not {"provider_item_id", "published_at"} <= set(value) <= _DISPLAY_METADATA_FIELDS:
        return False
    item_id = value.get("provider_item_id")
    published_at = value.get("published_at")
    if not all(isinstance(item, str) and item for item in (item_id, published_at)):
        return False
    title = value.get("display_title")
    if title is not None and (
        not isinstance(title, str) or not title or len(title) > 2000 or _SECRET_VALUE.search(title)
    ):
        return False
    summary = value.get("display_summary")
    if summary is not None and (
        not isinstance(summary, str)
        or not summary
        or len(summary) > 1000
        or _SECRET_VALUE.search(summary)
    ):
        return False
    structured = value.get("structured_facts")
    if structured is not None and (
        not isinstance(structured, Mapping)
        or len(structured) > 16
        or _SECRET_VALUE.search(str(structured))
    ):
        return False
    url = value.get("display_url")
    if url is None:
        return True
    if not isinstance(url, str) or len(url) > 4000 or _SECRET_VALUE.search(url):
        return False
    parsed = _PUBLIC_URL.fullmatch(url)
    return parsed is not None and "@" not in parsed.group(1)


class EndToEndMockEvidencePipeline:
    """Run mock collection, then explicitly trigger evidence for persisted RawItems."""

    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        runner: CollectionRunner,
        sidecar: InMemoryProviderProjectionSidecar,
        event_enqueuer: EvidenceEventEnqueuer | None = None,
    ) -> None:
        self._factory = factory
        self._runner = runner
        self._sidecar = sidecar
        self._event_enqueuer = event_enqueuer

    async def run(
        self,
        target: CollectionTarget,
        *,
        collection_run_id: uuid.UUID | None = None,
        attempt: int = 0,
    ) -> EndToEndOutcome:
        try:
            collection = await self._runner.run(
                target,
                collection_run_id=collection_run_id,
                attempt=attempt,
            )
        except ClassifiedCollectionError:
            return _collection_failure(None, "collection_target_rejected")
        if collection.status != "succeeded" or collection.collection_run_id is None:
            return _collection_failure(
                collection.collection_run_id,
                "collection_not_succeeded",
                retry_delay=collection.retry_delay,
            )
        return await self.process_run(collection.collection_run_id)

    async def process_run(self, collection_run_id: uuid.UUID) -> EndToEndOutcome:
        async with self._factory() as session:
            raw_ids = tuple(
                await session.scalars(
                    select(RawItem.id)
                    .where(RawItem.collection_run_id == collection_run_id)
                    .order_by(RawItem.id)
                )
            )
            store = InMemoryEvidenceProjectionStore()
            reader = SqlAlchemyRawItemProjectionReader(session)
            trigger = RawItemEvidencePipelineTrigger(
                reader,
                store,
                EvidencePipelineService(EvidenceWriteService(session)),
            )
            outcomes: list[EvidenceTriggerOutcome] = []
            safe_errors: list[EndToEndError] = []
            for raw_id in raw_ids:
                raw_item = await reader.get(raw_id)
                projection = None if raw_item is None else self._sidecar.bind(raw_item)
                if projection is None:
                    safe_errors.append(
                        EndToEndError("projection_sidecar_missing", "projection_sidecar_missing")
                    )
                    continue
                try:
                    store.save(projection)
                except ValueError:
                    safe_errors.append(
                        EndToEndError("projection_sidecar_invalid", "projection_sidecar_invalid")
                    )
                    continue
                outcomes.append(await trigger.trigger(raw_id))
            await session.commit()
        if self._event_enqueuer is not None:
            for outcome in outcomes:
                evidence_id = (
                    outcome.pipeline_outcome.evidence_item_id
                    if outcome.pipeline_outcome is not None
                    else None
                )
                if evidence_id is None:
                    continue
                try:
                    self._event_enqueuer.enqueue(evidence_id)
                except Exception:
                    # The Evidence transaction is complete; dispatch cannot roll it back.
                    continue
        return EndToEndOutcome(
            status=EndToEndStatus.PROCESSED if not safe_errors else EndToEndStatus.INVALID,
            collection_run_id=collection_run_id,
            raw_item_count=len(raw_ids),
            trigger_outcomes=tuple(outcomes),
            safe_errors=tuple(safe_errors),
        )


def _collection_failure(
    run_id: uuid.UUID | None, code: str, *, retry_delay: float | None = None
) -> EndToEndOutcome:
    return EndToEndOutcome(
        status=EndToEndStatus.COLLECTION_FAILED,
        collection_run_id=run_id,
        raw_item_count=0,
        safe_errors=(EndToEndError(code, code),),
        retry_delay=retry_delay,
    )
