"""Content-free projection storage and minimal RawItem read boundary."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Protocol, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from market_intelligence.db.models import RawItem
from market_intelligence.evidence.orchestration import validate_provider_projection


@dataclass(frozen=True, slots=True)
class RawItemEvidenceProjection:
    raw_item_id: uuid.UUID
    source_id: uuid.UUID
    source_account_id: uuid.UUID | None
    provider: str
    sanitized_projection: Mapping[str, object]
    observed_at: datetime
    correlation_id: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "sanitized_projection",
            MappingProxyType(dict(self.sanitized_projection)),
        )


@dataclass(frozen=True, slots=True)
class SafeRawItemProjectionSource:
    raw_item_id: uuid.UUID
    source_id: uuid.UUID
    source_account_id: uuid.UUID | None
    external_id: str | None
    fetched_at: datetime
    payload_hash: str | None
    payload_location: str | None


class EvidenceProjectionStore(Protocol):
    def save(self, projection: RawItemEvidenceProjection) -> None: ...

    def get(self, raw_item_id: uuid.UUID) -> RawItemEvidenceProjection | None: ...


class RawItemProjectionReader(Protocol):
    async def get(self, raw_item_id: uuid.UUID) -> SafeRawItemProjectionSource | None: ...


@dataclass(slots=True)
class InMemoryEvidenceProjectionStore:
    """Explicit synthetic store; no filesystem, raw payload, or provider IO."""

    _items: dict[uuid.UUID, RawItemEvidenceProjection] = field(default_factory=dict)

    def save(self, projection: RawItemEvidenceProjection) -> None:
        if (
            validate_provider_projection(projection.provider, projection.sanitized_projection)
            is None
        ):
            raise ValueError("projection_invalid")
        if projection.observed_at.tzinfo is None:
            raise ValueError("projection_observed_at_invalid")
        if projection.raw_item_id in self._items:
            raise ValueError("projection_already_exists")
        self._items[projection.raw_item_id] = projection

    def get(self, raw_item_id: uuid.UUID) -> RawItemEvidenceProjection | None:
        return self._items.get(raw_item_id)


class SqlAlchemyRawItemProjectionReader:
    """Read only the RawItem fields needed to verify a stored projection."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, raw_item_id: uuid.UUID) -> SafeRawItemProjectionSource | None:
        row = (
            await self._session.execute(
                select(
                    RawItem.id,
                    RawItem.source_id,
                    RawItem.source_account_id,
                    RawItem.external_id,
                    RawItem.fetched_at,
                    RawItem.payload_hash,
                    RawItem.payload_location,
                ).where(RawItem.id == raw_item_id)
            )
        ).one_or_none()
        if row is None:
            return None
        return SafeRawItemProjectionSource(
            raw_item_id=cast(uuid.UUID, row.id),
            source_id=cast(uuid.UUID, row.source_id),
            source_account_id=cast(uuid.UUID | None, row.source_account_id),
            external_id=cast(str | None, row.external_id),
            fetched_at=cast(datetime, row.fetched_at),
            payload_hash=cast(str | None, row.payload_hash),
            payload_location=cast(str | None, row.payload_location),
        )
