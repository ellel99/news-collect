"""Explicit mock collection-to-evidence orchestration with no provider IO."""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

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

_EVIDENCE_METADATA_FIELDS = frozenset(
    {
        "provider_item_id",
        "published_at",
        "field_names",
        "has_title",
        "has_description",
        "has_snippet",
        "has_source_url",
    }
)
_DISPLAY_METADATA_FIELDS = frozenset(
    {"provider_item_id", "published_at", "display_title", "display_url"}
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


@dataclass(frozen=True, slots=True)
class EndToEndOutcome:
    status: EndToEndStatus
    collection_run_id: uuid.UUID | None
    raw_item_count: int
    trigger_outcomes: tuple[EvidenceTriggerOutcome, ...] = ()
    safe_errors: tuple[EndToEndError, ...] = ()


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
        if result.provider != "marketaux":
            raise ValueError("provider_unsupported")
        if len(result.display_projections) != len(result.raw_items):
            raise ValueError("provider_display_projection_missing")
        for item, metadata, display in zip(
            result.raw_items,
            result.sanitized_metadata,
            result.display_projections,
            strict=True,
        ):
            if set(metadata) != _EVIDENCE_METADATA_FIELDS:
                raise ValueError("provider_evidence_projection_invalid")
            if not _valid_display_projection(display):
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
        projection = {
            key: value for key, value in item.metadata.items() if key in _EVIDENCE_METADATA_FIELDS
        }
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
    ) -> None:
        self._factory = factory
        self._runner = runner
        self._sidecar = sidecar

    async def run(self, target: CollectionTarget) -> EndToEndOutcome:
        try:
            collection = await self._runner.run(target)
        except ClassifiedCollectionError:
            return _collection_failure(None, "collection_target_rejected")
        if collection.status != "succeeded" or collection.collection_run_id is None:
            return _collection_failure(collection.collection_run_id, "collection_not_succeeded")
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
        return EndToEndOutcome(
            status=EndToEndStatus.PROCESSED if not safe_errors else EndToEndStatus.INVALID,
            collection_run_id=collection_run_id,
            raw_item_count=len(raw_ids),
            trigger_outcomes=tuple(outcomes),
            safe_errors=tuple(safe_errors),
        )


def _collection_failure(run_id: uuid.UUID | None, code: str) -> EndToEndOutcome:
    return EndToEndOutcome(
        status=EndToEndStatus.COLLECTION_FAILED,
        collection_run_id=run_id,
        raw_item_count=0,
        safe_errors=(EndToEndError(code, code),),
    )
