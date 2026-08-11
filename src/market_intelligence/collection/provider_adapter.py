"""Narrow bridge from provider scaffolds to the existing collection contract."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from market_intelligence.collection.contracts import FetchBatch, FetchRequest
from market_intelligence.collection.errors import ClassifiedCollectionError, CollectionErrorCode
from market_intelligence.providers.contracts import (
    ProviderAdapter,
    ProviderAdapterError,
    ProviderAdapterErrorCode,
    ProviderFetchRequest,
    ProviderFetchResult,
    ProviderTransport,
)


class ProviderResultObserver(Protocol):
    """Receive a content-free provider result before collection persistence."""

    def observe(self, result: ProviderFetchResult) -> None: ...


class CursorRelation(StrEnum):
    ADVANCE = "advance"
    UNCHANGED = "unchanged"
    INVALID = "invalid"


class ProviderCursorPolicy(Protocol):
    def classify(self, current: str | None, candidate: str) -> CursorRelation: ...


class StrictCursorPolicy:
    """Require every non-initial provider cursor to advance."""

    def classify(self, current: str | None, candidate: str) -> CursorRelation:
        candidate_value = _cursor_value(candidate)
        if candidate_value is None:
            return CursorRelation.INVALID
        if current is None:
            return CursorRelation.ADVANCE
        current_value = _cursor_value(current)
        if current_value is None or candidate_value <= current_value:
            return CursorRelation.INVALID
        return CursorRelation.ADVANCE


class SnapshotCursorPolicy:
    """Allow a snapshot endpoint to repeat its latest cursor without advancing."""

    def classify(self, current: str | None, candidate: str) -> CursorRelation:
        candidate_value = _cursor_value(candidate)
        if candidate_value is None:
            return CursorRelation.INVALID
        if current is None:
            return CursorRelation.ADVANCE
        current_value = _cursor_value(current)
        if current_value is None or candidate_value < current_value:
            return CursorRelation.INVALID
        if candidate_value == current_value:
            return CursorRelation.UNCHANGED
        return CursorRelation.ADVANCE


_STRICT_CURSOR_POLICY = StrictCursorPolicy()
_SNAPSHOT_CURSOR_POLICY = SnapshotCursorPolicy()
_CURSOR_POLICIES: Mapping[str, ProviderCursorPolicy] = {
    "sec_edgar": _SNAPSHOT_CURSOR_POLICY,
}


class ProviderCollectionAdapter:
    """Adapt a provider scaffold without giving it persistence responsibilities."""

    cursor_type = "provider_cursor_v1"

    def __init__(
        self,
        adapter: ProviderAdapter,
        transport: ProviderTransport,
        observer: ProviderResultObserver | None = None,
    ) -> None:
        self._adapter = adapter
        self._transport = transport
        self._observer = observer
        self._cursor_policy = _CURSOR_POLICIES.get(adapter.provider_key, _STRICT_CURSOR_POLICY)

    async def fetch(self, request: FetchRequest) -> FetchBatch:
        result = await self._adapter.fetch(
            ProviderFetchRequest(
                source_id=request.target.source_id,
                source_account_id=request.target.source_account_id,
                cursor=request.cursor.cursor_value,
                config=request.target.collection_options,
                limit=request.batch_limit,
                deadline_at=request.deadline_at,
                correlation_id=f"collection:{request.target.source_id}",
            ),
            self._transport,
        )
        if result.safe_errors:
            raise _collection_error(result.safe_errors[0])
        if result.provider != self._adapter.provider_key:
            raise ClassifiedCollectionError(
                CollectionErrorCode.CONTRACT_INVALID,
                "provider result identity mismatch",
            )
        if (
            result.next_cursor is not None
            and self._cursor_policy.classify(request.cursor.cursor_value, result.next_cursor)
            is CursorRelation.UNCHANGED
        ):
            return FetchBatch(
                items=(),
                next_cursor=result.next_cursor,
                last_published_at=request.cursor.last_published_at,
                has_more=False,
            )
        if self._observer is not None:
            try:
                self._observer.observe(result)
            except (TypeError, ValueError) as exc:
                raise ClassifiedCollectionError(
                    CollectionErrorCode.CONTRACT_INVALID,
                    "provider projection sidecar rejected result",
                ) from exc
        return FetchBatch(
            items=result.raw_items,
            next_cursor=result.next_cursor,
            last_published_at=_last_published_at(result.sanitized_metadata),
            has_more=result.has_more,
        )

    def is_cursor_successor(self, current: str | None, candidate: str) -> bool:
        return self._cursor_policy.classify(current, candidate) is not CursorRelation.INVALID


def _collection_error(error: ProviderAdapterError) -> ClassifiedCollectionError:
    code = {
        ProviderAdapterErrorCode.CONFIG_INVALID: CollectionErrorCode.CONFIG_INVALID,
        ProviderAdapterErrorCode.CONTRACT_INVALID: CollectionErrorCode.CONTRACT_INVALID,
        ProviderAdapterErrorCode.RATE_LIMITED: CollectionErrorCode.RATE_LIMITED,
        ProviderAdapterErrorCode.TIMEOUT: CollectionErrorCode.TIMEOUT,
        ProviderAdapterErrorCode.UPSTREAM_ERROR: (
            CollectionErrorCode.UPSTREAM_RETRYABLE
            if error.retryable
            else CollectionErrorCode.CONTRACT_INVALID
        ),
    }[error.code]
    return ClassifiedCollectionError(
        code,
        error.safe_message,
        retry_after=error.retry_after_seconds,
    )


def _cursor_value(cursor: str) -> tuple[str, str] | None:
    try:
        value = json.loads(cursor)
    except (TypeError, ValueError):
        return None
    if not isinstance(value, dict):
        return None
    published_at = value.get("published_at")
    provider_item_id = value.get("provider_item_id")
    if not isinstance(published_at, str) or not isinstance(provider_item_id, str):
        return None
    return published_at, provider_item_id


def _last_published_at(metadata: tuple[Mapping[str, object], ...]) -> datetime | None:
    parsed: list[datetime] = []
    for item in metadata:
        value = item.get("published_at")
        if not isinstance(value, str):
            continue
        try:
            timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            continue
        if timestamp.tzinfo is not None:
            parsed.append(timestamp)
    return max(parsed, default=None)
