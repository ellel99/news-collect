"""Typed v1 factual payload contracts for the four approved operations."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

_SECRET = re.compile(
    r"(?i)(api[_-]?key|api[_-]?token|authorization|x-finnhub-token|token|secret|password)"
)
_SEC_ARCHIVE = re.compile(r"^https://www\.sec\.gov/Archives/[A-Za-z0-9._~!$&'()*+,;=:@%/-]+$")


class ProjectionContractError(ValueError):
    pass


def canonical_projection_hash(payload: Mapping[str, Any]) -> str:
    try:
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode()
    except (TypeError, ValueError):
        raise ProjectionContractError("projection_payload_not_canonical_json") from None
    return hashlib.sha256(encoded).hexdigest()


def validate_factual_payload(
    provider: str,
    operation_key: str,
    schema_version: int,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    if schema_version != 1 or not isinstance(payload, Mapping):
        raise ProjectionContractError("projection_contract_unknown")
    _reject_unsafe(payload)
    key = (provider, operation_key)
    if key == ("marketaux", "news_all"):
        return _marketaux(payload)
    if key == ("finnhub", "quote"):
        return _finnhub(payload)
    if key == ("eia", "electricity_retail_sales"):
        return _eia(payload)
    if key == ("sec_edgar", "submissions_recent"):
        return _sec(payload)
    raise ProjectionContractError("projection_contract_unknown")


def _exact(payload: Mapping[str, Any], keys: set[str]) -> dict[str, Any]:
    if set(payload) != keys:
        raise ProjectionContractError("projection_payload_fields_invalid")
    return dict(payload)


def _text(value: object, *, maximum: int = 2000, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise ProjectionContractError("projection_text_invalid")
    normalized = " ".join(value.split())
    if not normalized or len(normalized) > maximum or _SECRET.search(normalized):
        raise ProjectionContractError("projection_text_invalid")
    return normalized


def _number(value: object) -> int | float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
    ):
        raise ProjectionContractError("projection_number_invalid")
    return value


def _marketaux(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = _exact(
        payload,
        {
            "provider_item_id",
            "published_at",
            "title",
            "canonical_url",
            "source_identity",
            "query",
            "language",
            "symbols",
            "description_coverage",
            "snippet_coverage",
        },
    )
    for key in ("provider_item_id", "published_at", "query"):
        result[key] = _text(result[key])
    result["title"] = _text(result["title"], optional=True)
    result["source_identity"] = _text(result["source_identity"], optional=True)
    result["language"] = _text(result["language"], maximum=12, optional=True)
    symbols = result["symbols"]
    if symbols is not None and (
        not isinstance(symbols, (list, tuple))
        or len(symbols) > 10
        or any(_text(item, maximum=20) is None for item in symbols)
    ):
        raise ProjectionContractError("projection_symbols_invalid")
    result["symbols"] = None if symbols is None else list(symbols)
    url = result["canonical_url"]
    if url is not None and not _public_url(url):
        raise ProjectionContractError("projection_url_invalid")
    if result["description_coverage"] != "blocked" or result["snippet_coverage"] != "blocked":
        raise ProjectionContractError("projection_coverage_invalid")
    return result


def _finnhub(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = _exact(
        payload,
        {
            "provider_item_id",
            "published_at",
            "symbol",
            "provider_timestamp",
            "c",
            "d",
            "dp",
            "h",
            "l",
            "o",
            "pc",
            "currency",
            "exchange",
        },
    )
    for key in ("provider_item_id", "published_at", "symbol", "currency", "exchange"):
        result[key] = _text(result[key], maximum=100)
    result["provider_timestamp"] = _number(result["provider_timestamp"])
    for key in ("c", "d", "dp", "h", "l", "o", "pc"):
        result[key] = _number(result[key])
    return result


def _eia(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = _exact(
        payload,
        {
            "provider_item_id",
            "published_at",
            "period",
            "dataset",
            "series_identity",
            "geography",
            "sector",
            "metric",
            "value",
            "unit",
        },
    )
    for key in set(result) - {"value"}:
        result[key] = _text(result[key], maximum=255)
    result["value"] = _number(result["value"])
    return result


def _sec(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = _exact(
        payload,
        {
            "provider_item_id",
            "published_at",
            "cik",
            "ticker",
            "accession_number",
            "filing_date",
            "form",
            "primary_document",
            "official_url",
            "official_source",
        },
    )
    for key in set(result) - {"official_source"}:
        result[key] = _text(result[key], maximum=4000)
    if result["official_source"] is not True or not _SEC_ARCHIVE.fullmatch(result["official_url"]):
        raise ProjectionContractError("projection_sec_reference_invalid")
    if "/" in result["primary_document"] or ".." in result["primary_document"]:
        raise ProjectionContractError("projection_sec_reference_invalid")
    return result


def _public_url(value: object) -> bool:
    if not isinstance(value, str) or len(value) > 4000 or _SECRET.search(value):
        return False
    parsed = urlsplit(value)
    return (
        parsed.scheme in {"http", "https"}
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
    )


def _reject_unsafe(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if _SECRET.search(str(key)):
                raise ProjectionContractError("projection_secret_marker_detected")
            _reject_unsafe(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _reject_unsafe(child)
    elif isinstance(value, str) and _SECRET.search(value):
        raise ProjectionContractError("projection_secret_marker_detected")
