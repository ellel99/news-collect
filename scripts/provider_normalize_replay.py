#!/usr/bin/env python3
"""Build content-free normalization candidates from local provider captures."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

CANDIDATE_VERSION = 1
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CAPTURE_ROOT = REPOSITORY_ROOT / "local_evaluation" / "raw_provider_captures"
PROVIDER_ORDER = ("marketaux", "finnhub", "eia", "sec_edgar")
SUPPORTED_PROVIDERS = frozenset(PROVIDER_ORDER)
PROVIDER_ITEM_TYPES = {
    "marketaux": "marketaux_news",
    "finnhub": "finnhub_quote",
    "eia": "eia_energy_timeseries",
    "sec_edgar": "sec_filing",
}
FORBIDDEN_SECRET_KEYS = frozenset(
    {"api_key", "api_token", "token", "authorization", "x-finnhub-token"}
)
SECRET_QUERY_MARKERS = ("api_key=", "api_token=", "token=")
PRICE_FIELDS = frozenset({"c", "d", "dp", "h", "l", "o", "pc"})


class ReplayCandidateError(ValueError):
    """Fail-closed error whose message is safe to emit."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build content-free replay candidates")
    parser.add_argument("capture_file", nargs="?", type=Path)
    parser.add_argument("--capture-dir", type=Path)
    return parser


def _has_value(item: dict[str, Any], field: str) -> bool:
    return item.get(field) not in (None, "", [], {})


def _has_any(item: dict[str, Any], fields: Sequence[str]) -> bool:
    return any(_has_value(item, field) for field in fields)


def _contains_secret_risk(value: object) -> bool:
    if isinstance(value, dict):
        return any(
            str(key).lower() in FORBIDDEN_SECRET_KEYS or _contains_secret_risk(child)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_contains_secret_risk(child) for child in value)
    if isinstance(value, str):
        lowered = value.lower()
        return any(marker in lowered for marker in SECRET_QUERY_MARKERS)
    return False


def _items(capture: dict[str, Any], provider: str) -> list[dict[str, Any]]:
    body = capture.get("response_body")
    if not isinstance(body, dict):
        return []
    if provider == "marketaux":
        data = body.get("data")
        return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []
    if provider == "finnhub":
        return [body] if body else []
    if provider == "eia":
        response = body.get("response")
        data = response.get("data") if isinstance(response, dict) else None
        return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []
    if provider == "sec_edgar":
        filings = body.get("filings")
        recent = filings.get("recent") if isinstance(filings, dict) else None
        if not isinstance(recent, dict):
            return []
        lengths = [len(column) for column in recent.values() if isinstance(column, list)]
        return [
            {
                key: column[index]
                for key, column in recent.items()
                if isinstance(column, list) and index < len(column)
            }
            for index in range(max(lengths, default=0))
        ]
    return []


def _context(capture: dict[str, Any]) -> dict[str, Any]:
    request = capture.get("request")
    context = request.get("context") if isinstance(request, dict) else None
    return context if isinstance(context, dict) else {}


def _stable_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _envelope(
    provider: str,
    item: dict[str, Any],
    context: dict[str, Any],
    capture_hash: str,
    observed_time_available: bool,
) -> dict[str, object]:
    if provider == "marketaux":
        item_id = _has_value(item, "uuid")
        event_time = _has_value(item, "published_at")
        source = _has_value(item, "source")
        entity = _has_value(item, "entities")
        asset = entity
        dedup = item_id
        content = _has_any(item, ("title", "snippet", "description"))
        numeric = False
    elif provider == "finnhub":
        symbol = context.get("symbol") not in (None, "")
        event_time = _has_value(item, "t")
        item_id = symbol and event_time
        source = True
        entity = symbol
        asset = symbol
        dedup = item_id
        content = False
        numeric = _has_any(item, tuple(PRICE_FIELDS))
    elif provider == "eia":
        event_time = _has_value(item, "period")
        geography = _has_any(item, ("stateid", "stateDescription"))
        sector = _has_any(item, ("sectorid", "sectorName"))
        item_id = event_time and geography and sector
        source = True
        entity = geography or sector
        asset = False
        dedup = item_id
        content = False
        numeric = _has_any(item, ("price", "value"))
    else:
        ticker = context.get("ticker") not in (None, "")
        item_id = _has_value(item, "accessionNumber")
        event_time = _has_any(item, ("acceptanceDateTime", "filingDate", "reportDate"))
        source = True
        entity = ticker
        asset = ticker
        dedup = item_id
        content = False
        numeric = False
    return {
        "normalized_candidate_version": CANDIDATE_VERSION,
        "provider": provider,
        "provider_item_type": PROVIDER_ITEM_TYPES[provider],
        "source_capture_hash": capture_hash,
        "provider_item_id_available": item_id,
        "provider_item_hash": _stable_hash({"provider": provider, "item": item}),
        "event_time_available": event_time,
        "observed_time_available": observed_time_available,
        "source_available": source,
        "entity_available": entity,
        "asset_or_company_available": asset,
        "dedup_key_available": dedup,
        "content_text_available": content,
        "numeric_value_available": numeric,
        "official_source_flag": provider in {"eia", "sec_edgar"},
        "market_data_flag": provider == "finnhub",
        "disclosure_flag": provider == "sec_edgar",
        "news_signal_flag": provider == "marketaux",
        "errors": [],
    }


def _count(items: list[dict[str, Any]], field: str) -> int:
    return sum(_has_value(item, field) for item in items)


def _provider_summary(
    provider: str,
    items: list[dict[str, Any]],
    context: dict[str, Any],
    envelopes: list[dict[str, object]],
) -> dict[str, object]:
    common: dict[str, object] = {
        "input_items": len(items),
        "candidate_items": len(envelopes),
        "content_values_emitted": False,
        "errors": [],
    }
    if provider == "marketaux":
        return {
            **common,
            "uuid_available_count": _count(items, "uuid"),
            "title_available_count": _count(items, "title"),
            "url_available_count": _count(items, "url"),
            "published_at_available_count": _count(items, "published_at"),
            "snippet_available_count": _count(items, "snippet"),
            "description_available_count": _count(items, "description"),
            "source_available_count": _count(items, "source"),
            "entities_available_count": _count(items, "entities"),
            "keywords_available_count": _count(items, "keywords"),
            "language_available_count": _count(items, "language"),
            "dedup_key_available_count": sum(
                bool(envelope["dedup_key_available"]) for envelope in envelopes
            ),
            "timestamp_available_count": sum(
                bool(envelope["event_time_available"]) for envelope in envelopes
            ),
        }
    if provider == "finnhub":
        coverage = {field: _count(items, field) for field in sorted(PRICE_FIELDS)}
        return {
            **common,
            "symbol_available_count": len(items) if context.get("symbol") not in (None, "") else 0,
            "quote_timestamp_available_count": _count(items, "t"),
            "numeric_value_field_count": sum(coverage.values()),
            "price_field_coverage": coverage,
            "market_data_flag": True,
        }
    if provider == "eia":
        return {
            **common,
            "period_available_count": _count(items, "period"),
            "geography_available_count": sum(
                _has_any(item, ("stateid", "stateDescription")) for item in items
            ),
            "sector_available_count": sum(
                _has_any(item, ("sectorid", "sectorName")) for item in items
            ),
            "numeric_value_field_count": sum(_has_any(item, ("price", "value")) for item in items),
            "official_source_flag": True,
            "energy_evidence_flag": True,
        }
    ticker_available = context.get("ticker") not in (None, "")
    return {
        **common,
        "accession_available_count": _count(items, "accessionNumber"),
        "form_available_count": _count(items, "form"),
        "filing_date_available_count": _count(items, "filingDate"),
        "acceptance_time_available_count": _count(items, "acceptanceDateTime"),
        "ticker_available_count": len(items) if ticker_available else 0,
        "disclosure_flag": True,
        "official_source_flag": True,
    }


def normalize_capture(capture: dict[str, Any], capture_hash: str) -> dict[str, object]:
    if _contains_secret_risk(capture):
        raise ReplayCandidateError("secret_risk_detected")
    provider = capture.get("provider")
    if not isinstance(provider, str) or provider not in SUPPORTED_PROVIDERS:
        raise ReplayCandidateError("unknown_provider")
    items = _items(capture, provider)
    if not items:
        raise ReplayCandidateError("no_input_items")
    context = _context(capture)
    observed = capture.get("captured_at") not in (None, "")
    envelopes = [_envelope(provider, item, context, capture_hash, observed) for item in items]
    return {
        "candidate_version": CANDIDATE_VERSION,
        "provider": provider,
        "source_capture_hash": capture_hash,
        "input_items": len(items),
        "candidate_items": len(envelopes),
        "content_values_emitted": False,
        "common_envelope_candidates": envelopes,
        "provider_summary": _provider_summary(provider, items, context, envelopes),
        "errors": [],
    }


def _load_capture(path: Path) -> dict[str, Any]:
    root = CAPTURE_ROOT.resolve()
    resolved = path.resolve()
    if resolved.parent != root:
        raise ReplayCandidateError("capture_path_outside_local_evaluation")
    try:
        raw = resolved.read_bytes()
        loaded: object = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReplayCandidateError("invalid_capture") from exc
    if not isinstance(loaded, dict):
        raise ReplayCandidateError("capture_must_be_object")
    return loaded


def normalize_file(path: Path) -> dict[str, object]:
    resolved = path.resolve()
    capture = _load_capture(resolved)
    return normalize_capture(capture, hashlib.sha256(resolved.read_bytes()).hexdigest())


def _coverage(envelopes: list[dict[str, object]]) -> dict[str, int]:
    fields = {
        "provider_item_id_available_count": "provider_item_id_available",
        "event_time_available_count": "event_time_available",
        "entity_available_count": "entity_available",
        "dedup_key_available_count": "dedup_key_available",
        "official_source_count": "official_source_flag",
        "market_data_count": "market_data_flag",
        "disclosure_count": "disclosure_flag",
        "news_signal_count": "news_signal_flag",
    }
    return {
        output: sum(bool(envelope[field]) for envelope in envelopes)
        for output, field in fields.items()
    }


def _merge_summaries(summaries: list[dict[str, object]]) -> dict[str, object]:
    merged: dict[str, object] = {
        "captures_seen": len(summaries),
        "content_values_emitted": False,
        "errors": [],
    }
    for summary in summaries:
        for key, value in summary.items():
            if key in {"content_values_emitted", "errors"}:
                continue
            if isinstance(value, bool):
                merged[key] = value
            elif isinstance(value, int):
                existing_count = merged.get(key, 0)
                merged[key] = existing_count + value if isinstance(existing_count, int) else value
            elif isinstance(value, dict):
                existing = merged.setdefault(key, {})
                if isinstance(existing, dict):
                    for nested_key, nested_value in value.items():
                        if isinstance(nested_value, int):
                            existing[str(nested_key)] = (
                                int(existing.get(str(nested_key), 0)) + nested_value
                            )
    return merged


def normalize_directory(path: Path) -> dict[str, object]:
    if path.resolve() != CAPTURE_ROOT.resolve():
        raise ReplayCandidateError("capture_path_outside_local_evaluation")
    files = sorted(path.glob("*.json"))
    if not files:
        raise ReplayCandidateError("no_captures_found")
    results = [normalize_file(file) for file in files]
    providers = {str(result["provider"]) for result in results}
    missing = SUPPORTED_PROVIDERS - providers
    if missing:
        raise ReplayCandidateError("missing_required_providers")
    summaries: dict[str, dict[str, object]] = {}
    envelopes: list[dict[str, object]] = []
    total_input_items = 0
    for provider in PROVIDER_ORDER:
        provider_results = [result for result in results if result["provider"] == provider]
        provider_envelopes: list[dict[str, object]] = []
        provider_summary_values: list[dict[str, object]] = []
        for result in provider_results:
            candidate_values = result["common_envelope_candidates"]
            if isinstance(candidate_values, list):
                provider_envelopes.extend(
                    value for value in candidate_values if isinstance(value, dict)
                )
            summary_value = result["provider_summary"]
            if isinstance(summary_value, dict):
                provider_summary_values.append(summary_value)
            input_items = result["input_items"]
            if isinstance(input_items, int):
                total_input_items += input_items
        summaries[provider] = _merge_summaries(provider_summary_values)
        envelopes.extend(provider_envelopes)
    type_counts = Counter(str(envelope["provider_item_type"]) for envelope in envelopes)
    return {
        "candidate_version": CANDIDATE_VERSION,
        "capture_files_seen": len(files),
        "providers_seen": [provider for provider in PROVIDER_ORDER if provider in providers],
        "total_input_items": total_input_items,
        "total_candidate_items": len(envelopes),
        "content_values_emitted": False,
        "provider_summaries": summaries,
        "common_envelope_coverage": _coverage(envelopes),
        "provider_type_counts": dict(sorted(type_counts.items())),
        "errors": [],
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if (args.capture_file is None) == (args.capture_dir is None):
        print(json.dumps({"errors": ["select_exactly_one_input"]}, sort_keys=True))
        return 2
    try:
        report = (
            normalize_directory(args.capture_dir)
            if args.capture_dir is not None
            else normalize_file(args.capture_file)
        )
    except ReplayCandidateError as exc:
        print(json.dumps({"errors": [str(exc)]}, sort_keys=True))
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
