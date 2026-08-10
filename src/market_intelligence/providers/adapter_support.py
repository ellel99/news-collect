"""Shared safe helpers for bounded provider adapters."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from market_intelligence.collection.contracts import RawItemEnvelope
from market_intelligence.providers.contracts import (
    ProviderAdapterError,
    ProviderAdapterErrorCode,
    ProviderFetchResult,
    ProviderTransportResponse,
)

SECRET_MARKER = re.compile(
    r"(?i)(api[_-]?key|api[_-]?token|authorization|x-finnhub-token|token|secret|password)"
)


def stable_hash(value: object) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=list
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def iso_timestamp(value: object) -> str | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(value, tz=UTC).isoformat()
        except (OSError, OverflowError, ValueError):
            return None
    if not isinstance(value, str) or not value:
        return None
    try:
        text = value.replace("Z", "+00:00")
        if len(text) == 7 and text[4] == "-":
            text = f"{text}-01"
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.replace(tzinfo=parsed.tzinfo or UTC).isoformat()


def raw_envelope(
    provider: str,
    item_id: str,
    projection: Mapping[str, object],
    response: ProviderTransportResponse,
    retention_class: str,
) -> RawItemEnvelope:
    payload_hash = stable_hash(projection)
    return RawItemEnvelope(
        external_id=item_id,
        fetched_at=response.received_at,
        http_status=response.status_code,
        content_type="application/json",
        payload_location=f"internal://provider/{provider}/{payload_hash}",
        payload_hash=payload_hash,
        retention_class=retention_class,
    )


def failed(
    provider: str, code: ProviderAdapterErrorCode, message: str, retryable: bool
) -> ProviderFetchResult:
    return ProviderFetchResult(
        raw_items=(),
        sanitized_metadata=(),
        next_cursor=None,
        has_more=False,
        safe_errors=(ProviderAdapterError(code, message, retryable),),
        provider=provider,
        contract_version=1,
    )


def response_error(provider: str, status_code: int) -> ProviderFetchResult | None:
    if status_code == 429:
        return failed(
            provider, ProviderAdapterErrorCode.RATE_LIMITED, "provider_rate_limited", True
        )
    if status_code >= 500:
        return failed(
            provider, ProviderAdapterErrorCode.UPSTREAM_ERROR, "provider_upstream_failed", True
        )
    if not 200 <= status_code < 300:
        return failed(
            provider, ProviderAdapterErrorCode.UPSTREAM_ERROR, "provider_request_rejected", False
        )
    return None


def safe_field_names(item: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(
        sorted(key for key in item if isinstance(key, str) and not SECRET_MARKER.search(key))
    )


def safe_identifier(value: object, *, max_length: int = 255) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized or len(normalized) > max_length or SECRET_MARKER.search(normalized):
        return None
    return normalized
