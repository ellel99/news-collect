#!/usr/bin/env python3
"""Read the recent visible Marketaux feed without provider or Telegram IO."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence

from market_intelligence.core.config import Settings
from market_intelligence.db.session import create_engine, create_session_factory
from market_intelligence.feed.marketaux_feed import MarketauxFeedService, VisibleFeedItem


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read recent Marketaux visible feed items")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--require-items", action="store_true")
    return parser


async def read_feed(limit: int, *, require_items: bool = False) -> tuple[dict[str, object], int]:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    engine = create_engine(settings)
    try:
        items = await MarketauxFeedService(create_session_factory(engine)).recent(limit)
    except Exception:
        return _report("BLOCKED", [], ["feed_read_failed"]), 2
    finally:
        await engine.dispose()
    rendered = [_item(item) for item in items]
    if require_items and not rendered:
        return _report("BLOCKED", [], ["visible_feed_empty"]), 2
    return _report("PASS", rendered, []), 0


def _item(item: VisibleFeedItem) -> dict[str, object]:
    return {
        "title": item.title,
        "source": item.source,
        "provider": item.provider,
        "published_at": item.published_at.isoformat(),
        "canonical_url": item.canonical_url,
        "provider_item_id": item.provider_item_id,
        "collected_at": item.collected_at.isoformat(),
        "raw_item_id": str(item.raw_item_id),
        "evidence_item_id": (None if item.evidence_item_id is None else str(item.evidence_item_id)),
    }


def _report(status: str, items: list[dict[str, object]], errors: list[str]) -> dict[str, object]:
    return {
        "provider": "marketaux",
        "status": status,
        "read_only": True,
        "item_count": len(items),
        "items": items,
        "response_saved": False,
        "safe_errors": errors,
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not 1 <= args.limit <= 50:
        report, exit_code = _report("BLOCKED", [], ["feed_limit_invalid"]), 2
    else:
        report, exit_code = asyncio.run(read_feed(args.limit, require_items=args.require_items))
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
