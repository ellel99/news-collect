"""Versioned cursor validation for the four approved R1 operations."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from market_intelligence.db.models import CollectionCursorStrategy


class CursorContractError(ValueError):
    pass


@dataclass(frozen=True, order=True, slots=True)
class CursorPosition:
    published_at: datetime
    provider_item_id: str


@dataclass(frozen=True, slots=True)
class CursorDecision:
    action: str
    candidate: CursorPosition | None


def decode_cursor(value: str | None) -> CursorPosition | None:
    if value is None:
        return None
    try:
        item = json.loads(value)
        published = datetime.fromisoformat(str(item["published_at"]).replace("Z", "+00:00"))
        identity = str(item["provider_item_id"])
    except (KeyError, TypeError, ValueError):
        raise CursorContractError("cursor_invalid") from None
    if published.tzinfo is None or not identity:
        raise CursorContractError("cursor_invalid")
    return CursorPosition(published, identity)


def decide_cursor(
    strategy: CollectionCursorStrategy, current: str | None, candidate: str | None
) -> CursorDecision:
    try:
        normalized_strategy = CollectionCursorStrategy(strategy)
    except ValueError:
        raise CursorContractError("cursor_strategy_invalid") from None
    if normalized_strategy not in {
        CollectionCursorStrategy.STRICT_INCREMENTAL,
        CollectionCursorStrategy.SNAPSHOT_WATERMARK,
        CollectionCursorStrategy.COMPOUND,
        CollectionCursorStrategy.REVISION,
    }:
        raise CursorContractError("cursor_strategy_invalid")
    previous, proposed = decode_cursor(current), decode_cursor(candidate)
    if proposed is None:
        return CursorDecision("unchanged", None)
    if previous is None or proposed > previous:
        return CursorDecision("advance", proposed)
    if proposed == previous and normalized_strategy in {
        CollectionCursorStrategy.SNAPSHOT_WATERMARK,
        CollectionCursorStrategy.REVISION,
        CollectionCursorStrategy.COMPOUND,
    }:
        return CursorDecision("no_new_items", proposed)
    raise CursorContractError("cursor_not_successor")
