#!/usr/bin/env python3
"""Produce content-free summaries from local provider captures."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CAPTURE_ROOT = REPOSITORY_ROOT / "local_evaluation" / "raw_provider_captures"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Summarize one local provider capture")
    parser.add_argument("capture_file", type=Path)
    return parser


def _items(capture: dict[str, Any]) -> list[dict[str, Any]]:
    provider = capture.get("provider")
    body = capture.get("response_body")
    if not isinstance(body, dict):
        return []
    if provider == "marketaux":
        data = body.get("data")
        return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []
    if provider == "finnhub":
        return [body]
    if provider == "eia":
        response = body.get("response")
        data = response.get("data") if isinstance(response, dict) else None
        return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []
    if provider == "sec_edgar":
        filings = body.get("filings")
        recent = filings.get("recent") if isinstance(filings, dict) else None
        if not isinstance(recent, dict):
            return []
        lengths = [len(value) for value in recent.values() if isinstance(value, list)]
        count = max(lengths, default=0)
        return [
            {
                key: value[index]
                for key, value in recent.items()
                if isinstance(value, list) and index < len(value)
            }
            for index in range(count)
        ]
    return []


def _required_fields(provider: str) -> tuple[str, ...]:
    return {
        "marketaux": ("uuid", "title", "url", "published_at"),
        "finnhub": ("c", "t"),
        "eia": ("period",),
        "sec_edgar": ("accessionNumber", "filingDate", "form"),
    }.get(provider, ())


def _dedup_available(provider: str, item: dict[str, Any], context: dict[str, Any]) -> bool:
    if provider == "marketaux":
        return bool(item.get("uuid"))
    if provider == "finnhub":
        return bool(context.get("symbol"))
    if provider == "eia":
        return bool(item.get("period") and item.get("stateid") and item.get("sectorid"))
    if provider == "sec_edgar":
        return bool(item.get("accessionNumber"))
    return False


def replay_summary(capture: dict[str, Any]) -> dict[str, object]:
    provider_value = capture.get("provider")
    provider = provider_value if isinstance(provider_value, str) else ""
    items = _items(capture)
    request = capture.get("request")
    context = request.get("context") if isinstance(request, dict) else {}
    if not isinstance(context, dict):
        context = {}
    required = _required_fields(provider)
    missing = {
        field: sum(1 for item in items if item.get(field) in (None, "")) for field in required
    }
    entity_candidates = {
        "marketaux": ("entities", "source"),
        "finnhub": ("symbol",),
        "eia": ("stateid", "sectorid"),
        "sec_edgar": ("cik", "ticker", "form"),
    }.get(provider, ())
    timestamp_candidates = {
        "marketaux": ("published_at",),
        "finnhub": ("t",),
        "eia": ("period",),
        "sec_edgar": ("filingDate", "acceptanceDateTime", "reportDate"),
    }.get(provider, ())
    item_fields = {key for item in items for key in item}
    context_fields = set(context)
    errors: list[str] = []
    if provider not in {"marketaux", "finnhub", "eia", "sec_edgar"}:
        errors.append("unknown_provider")
    if not items:
        errors.append("no_input_items")
    return {
        "provider": provider,
        "input_items": len(items),
        "normalized_items": 0,
        "missing_required_fields": missing,
        "dedup_key_available_count": sum(
            _dedup_available(provider, item, context) for item in items
        ),
        "entity_fields_available": sorted(
            field for field in entity_candidates if field in item_fields or field in context_fields
        ),
        "timestamp_fields_available": sorted(
            field for field in timestamp_candidates if field in item_fields
        ),
        "replay_ready": bool(items) and not errors,
        "errors": errors,
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    path = args.capture_file.resolve()
    root = CAPTURE_ROOT.resolve()
    if path.parent != root:
        print(json.dumps({"errors": ["capture_path_outside_local_evaluation"]}, sort_keys=True))
        return 2
    try:
        loaded: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        print(json.dumps({"errors": ["invalid_capture"]}, sort_keys=True))
        return 2
    if not isinstance(loaded, dict):
        print(json.dumps({"errors": ["capture_must_be_object"]}, sort_keys=True))
        return 2
    summary = replay_summary(loaded)
    print(json.dumps(summary, sort_keys=True))
    return 0 if summary["replay_ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
