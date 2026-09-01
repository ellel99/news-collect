"""Typed v1 factual payload contracts for the four approved operations."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import Any, Literal
from urllib.parse import urlsplit

_SECRET = re.compile(
    r"(?i)(api[_-]?key|api[_-]?token|authorization|x-finnhub-token|token|secret|password)"
)
_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.-]{0,19}$")
_LANGUAGE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})?$")
_MONTH = re.compile(r"^(\d{4})-(\d{2})$")
_CIK = re.compile(r"^\d{10}$")
_ACCESSION = re.compile(r"^\d{10}-\d{2}-\d{6}$")
_DOCUMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")

ProjectionQuality = Literal["complete", "partial"]


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
    if key == ("finnhub", "company_news"):
        return _company_news(payload)
    if key == ("eia", "electricity_retail_sales"):
        return _eia(payload)
    if key == ("eia", "electricity_rto_region_data"):
        return _rto(payload)
    if key == ("sec_edgar", "submissions_recent"):
        return _sec(payload)
    raise ProjectionContractError("projection_contract_unknown")


def normalize_and_classify_factual_payload(
    provider: str,
    operation_key: str,
    schema_version: int,
    payload: Mapping[str, Any],
) -> tuple[dict[str, Any], ProjectionQuality]:
    normalized = validate_factual_payload(provider, operation_key, schema_version, payload)
    if (provider, operation_key) in {("marketaux", "news_all"), ("finnhub", "company_news")}:
        quality: ProjectionQuality = (
            "partial"
            if any(
                normalized[field] is None for field in ("title", "canonical_url", "source_identity")
            )
            else "complete"
        )
    elif provider == "finnhub":
        quality = (
            "partial"
            if normalized["currency"] == "unknown" or normalized["exchange"] == "unknown"
            else "complete"
        )
    elif provider == "eia":
        quality = "partial" if normalized["unit"] == "unknown" else "complete"
    else:
        quality = "complete"
    return normalized, quality


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


def _timestamp(value: object) -> str:
    if not isinstance(value, str):
        raise ProjectionContractError("projection_timestamp_invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ProjectionContractError("projection_timestamp_invalid") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProjectionContractError("projection_timestamp_invalid")
    return parsed.astimezone(UTC).isoformat()


def _symbol(value: object) -> str:
    normalized = _text(value, maximum=20)
    assert normalized is not None
    normalized = normalized.upper()
    if not _SYMBOL.fullmatch(normalized):
        raise ProjectionContractError("projection_symbol_invalid")
    return normalized


def _opaque_id(value: object, *, maximum: int = 255) -> str:
    normalized = _text(value, maximum=maximum)
    assert normalized is not None
    if "://" in normalized or "/" in normalized or "\\" in normalized:
        raise ProjectionContractError("projection_provider_identity_invalid")
    return normalized


def _facet(value: object) -> str:
    normalized = _text(value, maximum=100)
    assert normalized is not None
    if "://" in normalized or "/" in normalized or "\\" in normalized:
        raise ProjectionContractError("projection_eia_facet_invalid")
    result = re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-")
    if not result:
        raise ProjectionContractError("projection_eia_facet_invalid")
    return result


def eia_series_identity(geography: object, sector: object, metric: object = "price") -> str:
    return f"electricity/retail-sales/{_facet(geography)}/{_facet(sector)}/{_facet(metric)}"


def sec_official_url(cik: str, accession: str, primary_document: str) -> str:
    return (
        "https://www.sec.gov/Archives/edgar/data/"
        f"{int(cik)}/{accession.replace('-', '')}/{primary_document}"
    )


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
    result["provider_item_id"] = _opaque_id(result["provider_item_id"])
    result["query"] = _text(result["query"], maximum=500)
    if "://" in result["query"]:
        raise ProjectionContractError("projection_query_invalid")
    result["published_at"] = _timestamp(result["published_at"])
    result["title"] = _text(result["title"], optional=True)
    result["source_identity"] = _text(result["source_identity"], optional=True)
    result["language"] = _text(result["language"], maximum=12, optional=True)
    if result["language"] is not None and not _LANGUAGE.fullmatch(result["language"]):
        raise ProjectionContractError("projection_language_invalid")
    if result["language"] is not None:
        result["language"] = result["language"].lower()
    symbols = result["symbols"]
    if symbols is not None and (
        not isinstance(symbols, (list, tuple))
        or len(symbols) > 10
        or any(not isinstance(item, str) for item in symbols)
    ):
        raise ProjectionContractError("projection_symbols_invalid")
    result["symbols"] = None if symbols is None else [_symbol(item) for item in symbols]
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
    result["published_at"] = _timestamp(result["published_at"])
    result["symbol"] = _symbol(result["symbol"])
    for key in ("currency", "exchange"):
        result[key] = _text(result[key], maximum=100)
    timestamp = result["provider_timestamp"]
    if not isinstance(timestamp, int) or isinstance(timestamp, bool) or timestamp <= 0:
        raise ProjectionContractError("projection_provider_timestamp_invalid")
    result["provider_timestamp"] = timestamp
    expected_id = f"{result['symbol']}:{timestamp}"
    if result["provider_item_id"] != expected_id:
        raise ProjectionContractError("projection_provider_identity_invalid")
    try:
        provider_time = datetime.fromtimestamp(timestamp, tz=UTC).isoformat()
    except (OSError, OverflowError, ValueError):
        raise ProjectionContractError("projection_provider_timestamp_invalid") from None
    if result["published_at"] != provider_time:
        raise ProjectionContractError("projection_provider_identity_invalid")
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
    result["provider_item_id"] = _opaque_id(result["provider_item_id"])
    result["published_at"] = _timestamp(result["published_at"])
    if not isinstance(result["period"], str):
        raise ProjectionContractError("projection_eia_period_invalid")
    period = result["period"]
    match = _MONTH.fullmatch(period)
    if match is None or not 1 <= int(match.group(2)) <= 12:
        raise ProjectionContractError("projection_eia_period_invalid")
    result["period"] = period
    result["dataset"] = _text(result["dataset"], maximum=100)
    if result["dataset"] != "electricity":
        raise ProjectionContractError("projection_eia_series_invalid")
    result["geography"] = _facet(result["geography"])
    result["sector"] = _facet(result["sector"])
    result["metric"] = _facet(result["metric"])
    expected_series = eia_series_identity(result["geography"], result["sector"], result["metric"])
    if result["series_identity"] != expected_series:
        raise ProjectionContractError("projection_eia_series_invalid")
    result["series_identity"] = expected_series
    result["unit"] = _text(result["unit"], maximum=100)
    result["value"] = _number(result["value"])
    return result


def _sec(payload: Mapping[str, Any]) -> dict[str, Any]:
    reference = payload.get("submissions_file")
    payload = dict(payload)
    payload.pop("submissions_file", None)
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
    result["published_at"] = _timestamp(result["published_at"])
    cik = _text(result["cik"], maximum=10)
    accession = _text(result["accession_number"], maximum=20)
    document = _text(result["primary_document"], maximum=255)
    assert cik is not None and accession is not None and document is not None
    if not _CIK.fullmatch(cik) or not _ACCESSION.fullmatch(accession):
        raise ProjectionContractError("projection_sec_reference_invalid")
    try:
        filing_date = date.fromisoformat(str(result["filing_date"]))
    except ValueError:
        raise ProjectionContractError("projection_sec_reference_invalid") from None
    if (
        str(filing_date) != result["filing_date"]
        or not _DOCUMENT.fullmatch(document)
        or ".." in document
    ):
        raise ProjectionContractError("projection_sec_reference_invalid")
    if result["provider_item_id"] != accession:
        raise ProjectionContractError("projection_provider_identity_invalid")
    result["ticker"] = _symbol(result["ticker"])
    result["form"] = _text(result["form"], maximum=50)
    result["cik"] = cik
    result["accession_number"] = accession
    result["primary_document"] = document
    expected_url = sec_official_url(cik, accession, document)
    if result["official_source"] is not True or result["official_url"] != expected_url:
        raise ProjectionContractError("projection_sec_reference_invalid")
    result["official_url"] = expected_url
    if reference is not None:
        if (
            not isinstance(reference, str)
            or not re.fullmatch(r"CIK\d{10}(?:-submissions-\d{3})?\.json", reference)
            or not reference.startswith(f"CIK{cik}")
        ):
            raise ProjectionContractError("projection_sec_reference_invalid")
        result["submissions_file"] = reference
    return result


def _company_news(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = _exact(
        payload,
        {
            "provider_item_id",
            "published_at",
            "title",
            "canonical_url",
            "source_identity",
            "symbol",
            "category",
            "summary_coverage",
        },
    )
    result["provider_item_id"] = _opaque_id(result["provider_item_id"])
    if not result["provider_item_id"].startswith("company-news:"):
        raise ProjectionContractError("projection_provider_identity_invalid")
    result["published_at"] = _timestamp(result["published_at"])
    result["symbol"] = _symbol(result["symbol"])
    for key in ("title", "source_identity", "category"):
        result[key] = _text(result[key], optional=True)
    if result["canonical_url"] is not None and not _public_url(result["canonical_url"]):
        raise ProjectionContractError("projection_url_invalid")
    if result["summary_coverage"] != "blocked":
        raise ProjectionContractError("projection_coverage_invalid")
    return result


def rto_series_identity(region: str, metric: str) -> str:
    if not re.fullmatch(r"[A-Z0-9-]{1,12}", region) or metric not in {"D", "NG"}:
        raise ProjectionContractError("projection_rto_series_invalid")
    return f"electricity/rto/region-data/{region}/{metric}"


def _rto(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = _exact(
        payload,
        {
            "provider_item_id",
            "published_at",
            "period",
            "dataset",
            "series_identity",
            "region",
            "metric",
            "value",
            "unit",
        },
    )
    if result["dataset"] != "electricity_rto_region_data":
        raise ProjectionContractError("projection_rto_series_invalid")
    period = result["period"]
    if not isinstance(period, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}", period):
        raise ProjectionContractError("projection_rto_period_invalid")
    timestamp = _timestamp(period + ":00:00+00:00")
    series = rto_series_identity(str(result["region"]), str(result["metric"]))
    identity = "rto:" + hashlib.sha256(f"{series}:{period}".encode()).hexdigest()
    if (
        result["series_identity"] != series
        or result["provider_item_id"] != identity
        or _timestamp(result["published_at"]) != timestamp
    ):
        raise ProjectionContractError("projection_rto_identity_invalid")
    result["published_at"] = timestamp
    result["value"] = _number(result["value"])
    result["unit"] = _text(result["unit"], maximum=100)
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
