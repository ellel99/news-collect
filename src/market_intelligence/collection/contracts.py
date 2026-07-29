from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Any, Protocol
from uuid import UUID


def _immutable_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(dict(value))


@dataclass(frozen=True, slots=True)
class CollectionTarget:
    source_id: UUID
    source_account_id: UUID | None
    source_type: str
    access_method: str
    retention_class: str
    collection_options: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "collection_options", _immutable_mapping(self.collection_options))


@dataclass(frozen=True, slots=True)
class CursorSnapshot:
    cursor_type: str | None
    cursor_value: str | None
    last_published_at: datetime | None


@dataclass(frozen=True, slots=True)
class FetchRequest:
    target: CollectionTarget
    cursor: CursorSnapshot
    batch_limit: int
    deadline_at: datetime


@dataclass(frozen=True, slots=True)
class RawItemEnvelope:
    external_id: str | None
    fetched_at: datetime
    http_status: int | None
    content_type: str | None
    payload_location: str | None
    payload_hash: str | None
    retention_class: str


@dataclass(frozen=True, slots=True)
class FetchBatch:
    items: tuple[RawItemEnvelope, ...] = ()
    next_cursor: str | None = None
    last_published_at: datetime | None = None
    has_more: bool = False


class CollectionAdapter(Protocol):
    @property
    def cursor_type(self) -> str | None: ...

    async def fetch(self, request: FetchRequest) -> FetchBatch: ...

    def is_cursor_successor(self, current: str | None, candidate: str) -> bool: ...
