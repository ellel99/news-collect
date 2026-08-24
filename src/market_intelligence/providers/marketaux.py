"""Marketaux adapter scaffold backed only by an injected provider transport."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Final
from urllib.parse import urlsplit

from market_intelligence.collection.contracts import RawItemEnvelope
from market_intelligence.providers.contracts import (
    ProviderAdapterError,
    ProviderAdapterErrorCode,
    ProviderFetchRequest,
    ProviderFetchResult,
    ProviderResponseTooLarge,
    ProviderTransport,
    ProviderTransportRequest,
    ProviderTransportTimeout,
)
from market_intelligence.providers.credentials import RuntimeCredential
from market_intelligence.safe_projection.contracts import (
    ProjectionContractError,
    validate_factual_payload,
)

_MAX_RECORDS: Final = 3
_CONTRACT_VERSION: Final = 1
_SECRET_MARKER = re.compile(
    r"(?i)(api[_-]?key|api[_-]?token|authorization|x-finnhub-token|token|secret|password)"
)
_SECRET_VALUE = re.compile(
    r"(?i)(api[_-]?key|api[_-]?token|authorization|x-finnhub-token|token|secret|password)="
)
_SAFE_CONFIG_KEYS = frozenset({"query", "language", "symbols", "timeout_seconds"})


class MarketauxAdapter:
    provider_key = "marketaux"
    contract_version = _CONTRACT_VERSION

    def __init__(self, credential: RuntimeCredential | None = None) -> None:
        self._credential = credential

    async def fetch(
        self,
        request: ProviderFetchRequest,
        transport: ProviderTransport,
    ) -> ProviderFetchResult:
        config_error = _validate_request(request)
        if config_error is not None:
            return _failed(config_error)

        timeout_seconds = float(request.config.get("timeout_seconds", 10.0))
        params: dict[str, str | int] = {
            "search": str(request.config.get("query", "")),
            "limit": request.limit,
            "page": 1,
        }
        language = request.config.get("language")
        if isinstance(language, str) and language:
            params["language"] = language
        symbols = request.config.get("symbols")
        if isinstance(symbols, (tuple, list)) and symbols:
            params["symbols"] = ",".join(str(symbol) for symbol in symbols)
        cursor = _decode_cursor(request.cursor)
        if request.cursor is not None and cursor is None:
            return _failed(
                ProviderAdapterError(
                    code=ProviderAdapterErrorCode.CONFIG_INVALID,
                    safe_message="provider_cursor_invalid",
                    retryable=False,
                )
            )
        if cursor is not None:
            published_after = _published_after_param(cursor[0])
            if published_after is None:
                return _failed(
                    ProviderAdapterError(
                        code=ProviderAdapterErrorCode.CONFIG_INVALID,
                        safe_message="provider_cursor_invalid",
                        retryable=False,
                    )
                )
            params["published_after"] = published_after

        transport_request = ProviderTransportRequest(
            provider=self.provider_key,
            operation="news_all",
            params=params,
            timeout_seconds=min(timeout_seconds, request.request_timeout_seconds),
            max_response_bytes=request.max_response_bytes,
            runtime_credential=self._credential,
        )
        try:
            response = await transport.send(transport_request)
        except ProviderTransportTimeout:
            return _failed(
                ProviderAdapterError(
                    code=ProviderAdapterErrorCode.TIMEOUT,
                    safe_message="provider_request_timed_out",
                    retryable=True,
                )
            )
        except ProviderResponseTooLarge:
            return _failed(
                ProviderAdapterError(
                    code=ProviderAdapterErrorCode.CONTRACT_INVALID,
                    safe_message="provider_response_too_large",
                    retryable=False,
                )
            )
        except Exception:
            return _failed(
                ProviderAdapterError(
                    code=ProviderAdapterErrorCode.UPSTREAM_ERROR,
                    safe_message="provider_transport_failed",
                    retryable=True,
                )
            )

        if response.status_code == 429:
            return _failed(
                ProviderAdapterError(
                    code=ProviderAdapterErrorCode.RATE_LIMITED,
                    safe_message="provider_rate_limited",
                    retryable=True,
                    retry_after_seconds=_retry_after(response.headers),
                )
            )
        if response.status_code >= 500:
            return _failed(
                ProviderAdapterError(
                    code=ProviderAdapterErrorCode.UPSTREAM_ERROR,
                    safe_message="provider_upstream_failed",
                    retryable=True,
                )
            )
        if not 200 <= response.status_code < 300:
            return _failed(
                ProviderAdapterError(
                    code=ProviderAdapterErrorCode.UPSTREAM_ERROR,
                    safe_message="provider_request_rejected",
                    retryable=False,
                )
            )

        items = _response_items(response.body)
        if items is None:
            return _failed(
                ProviderAdapterError(
                    code=ProviderAdapterErrorCode.CONTRACT_INVALID,
                    safe_message="provider_response_shape_invalid",
                    retryable=False,
                )
            )

        raw_items: list[RawItemEnvelope] = []
        metadata: list[Mapping[str, Any]] = []
        display_projections: list[Mapping[str, Any]] = []
        factual_projections: list[Mapping[str, Any]] = []
        cursor_candidates: list[tuple[str, str]] = []
        for item in items[: request.limit]:
            sanitized = _sanitize_item(item)
            if sanitized is None:
                return _failed(
                    ProviderAdapterError(
                        code=ProviderAdapterErrorCode.CONTRACT_INVALID,
                        safe_message="provider_item_identity_invalid",
                        retryable=False,
                    )
                )
            item_id = sanitized["provider_item_id"]
            published_at = sanitized["published_at"]
            projection: dict[str, Any] = {
                "provider_item_id": item_id,
                "published_at": published_at,
                "field_names": sanitized["field_names"],
                "has_title": sanitized["display_title"] is not None,
                "has_description": False,
                "has_snippet": False,
                "has_source_url": sanitized["display_url"] is not None,
            }
            factual = {
                "provider_item_id": item_id,
                "published_at": published_at,
                "title": sanitized["display_title"],
                "canonical_url": sanitized["display_url"],
                "source_identity": sanitized["source_identity"],
                "query": request.config.get("query"),
                "language": request.config.get("language"),
                "symbols": request.config.get("symbols"),
                "description_coverage": "blocked",
                "snippet_coverage": "blocked",
            }
            try:
                factual = validate_factual_payload(self.provider_key, "news_all", 1, factual)
            except ProjectionContractError:
                return _failed(
                    ProviderAdapterError(
                        code=ProviderAdapterErrorCode.CONTRACT_INVALID,
                        safe_message="provider_factual_contract_invalid",
                        retryable=False,
                    )
                )
            display: dict[str, Any] = {
                "provider_item_id": item_id,
                "published_at": published_at,
            }
            if sanitized["display_title"] is not None:
                display["display_title"] = sanitized["display_title"]
            if sanitized["display_url"] is not None:
                display["display_url"] = sanitized["display_url"]
            payload_hash = _stable_hash(projection)
            raw_items.append(
                RawItemEnvelope(
                    external_id=item_id,
                    fetched_at=response.received_at,
                    http_status=response.status_code,
                    content_type="application/json",
                    payload_location=f"internal://provider/marketaux/{payload_hash}",
                    payload_hash=payload_hash,
                    retention_class="metadata_only",
                )
            )
            metadata.append(projection)
            factual_projections.append(factual)
            display_projections.append(display)
            cursor_candidates.append((published_at, item_id))

        if not raw_items:
            return _failed(
                ProviderAdapterError(
                    code=ProviderAdapterErrorCode.CONTRACT_INVALID,
                    safe_message="provider_response_empty",
                    retryable=False,
                )
            )
        next_cursor = _encode_cursor(max(cursor_candidates))
        return ProviderFetchResult(
            raw_items=tuple(raw_items),
            sanitized_metadata=tuple(metadata),
            next_cursor=next_cursor,
            has_more=len(items) > request.limit,
            safe_errors=(),
            provider=self.provider_key,
            contract_version=self.contract_version,
            display_projections=tuple(display_projections),
            factual_projections=tuple(factual_projections),
        )


def _validate_request(request: ProviderFetchRequest) -> ProviderAdapterError | None:
    if request.limit < 1 or request.limit > _MAX_RECORDS:
        return ProviderAdapterError(
            code=ProviderAdapterErrorCode.CONFIG_INVALID,
            safe_message="provider_record_limit_invalid",
            retryable=False,
        )
    if request.deadline_at.tzinfo is None or request.deadline_at <= datetime.now(UTC):
        return ProviderAdapterError(
            code=ProviderAdapterErrorCode.CONFIG_INVALID,
            safe_message="provider_deadline_invalid",
            retryable=False,
        )
    if set(request.config) - _SAFE_CONFIG_KEYS:
        return ProviderAdapterError(
            code=ProviderAdapterErrorCode.CONFIG_INVALID,
            safe_message="provider_config_fields_invalid",
            retryable=False,
        )
    for key, value in request.config.items():
        if _SECRET_MARKER.search(key) or _SECRET_MARKER.search(str(value)):
            return ProviderAdapterError(
                code=ProviderAdapterErrorCode.CONFIG_INVALID,
                safe_message="provider_config_unsafe",
                retryable=False,
            )
    timeout = request.config.get("timeout_seconds", 10.0)
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
        return ProviderAdapterError(
            code=ProviderAdapterErrorCode.CONFIG_INVALID,
            safe_message="provider_timeout_invalid",
            retryable=False,
        )
    language = request.config.get("language")
    if language is not None and (
        not isinstance(language, str) or not language or _SECRET_MARKER.search(language)
    ):
        return ProviderAdapterError(
            code=ProviderAdapterErrorCode.CONFIG_INVALID,
            safe_message="provider_language_invalid",
            retryable=False,
        )
    symbols = request.config.get("symbols")
    if symbols is not None and (
        not isinstance(symbols, (tuple, list))
        or not symbols
        or len(symbols) > 10
        or any(
            not isinstance(symbol, str) or not symbol or _SECRET_MARKER.search(symbol)
            for symbol in symbols
        )
    ):
        return ProviderAdapterError(
            code=ProviderAdapterErrorCode.CONFIG_INVALID,
            safe_message="provider_symbols_invalid",
            retryable=False,
        )
    return None


def _response_items(body: object) -> list[Mapping[str, Any]] | None:
    if not isinstance(body, Mapping):
        return None
    data = body.get("data")
    if not isinstance(data, list) or any(not isinstance(item, Mapping) for item in data):
        return None
    return data


def _sanitize_item(item: Mapping[str, Any]) -> dict[str, Any] | None:
    item_id = item.get("uuid")
    published_at = item.get("published_at")
    if not isinstance(item_id, str) or not item_id or _SECRET_MARKER.search(item_id):
        return None
    if not isinstance(published_at, str) or not published_at or _SECRET_MARKER.search(published_at):
        return None
    title = _safe_title(item.get("title"))
    source_url = _safe_public_url(item.get("url"))
    source = item.get("source")
    if isinstance(source, Mapping):
        source = source.get("name") or source.get("domain")
    source_identity = _safe_title(source)
    return {
        "provider_item_id": item_id,
        "published_at": published_at,
        "field_names": tuple(
            sorted(key for key in item if isinstance(key, str) and not _SECRET_MARKER.search(key))
        ),
        "source_identity": source_identity,
        "display_title": title,
        "display_url": source_url,
    }


def _safe_title(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split())
    if not normalized or len(normalized) > 2000 or _SECRET_VALUE.search(normalized):
        return None
    return normalized


def _safe_public_url(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 4000 or _SECRET_VALUE.search(value):
        return None
    parsed = urlsplit(value)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    return value


def _stable_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=list,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _encode_cursor(candidate: tuple[str, str]) -> str:
    return json.dumps(
        {"provider_item_id": candidate[1], "published_at": candidate[0]},
        sort_keys=True,
        separators=(",", ":"),
    )


def _decode_cursor(cursor: str | None) -> tuple[str, str] | None:
    if cursor is None:
        return None
    try:
        value = json.loads(cursor)
    except (TypeError, ValueError):
        return None
    if not isinstance(value, dict):
        return None
    published_at = value.get("published_at")
    item_id = value.get("provider_item_id")
    if not isinstance(published_at, str) or not isinstance(item_id, str):
        return None
    if _SECRET_MARKER.search(published_at) or _SECRET_MARKER.search(item_id):
        return None
    return published_at, item_id


def _published_after_param(value: str) -> str | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S")


def _retry_after(headers: Mapping[str, str]) -> float | None:
    value = next(
        (header_value for name, header_value in headers.items() if name.lower() == "retry-after"),
        None,
    )
    if value is None:
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if 0 <= parsed <= 3600 else None


def _failed(error: ProviderAdapterError) -> ProviderFetchResult:
    return ProviderFetchResult(
        raw_items=(),
        sanitized_metadata=(),
        next_cursor=None,
        has_more=False,
        safe_errors=(error,),
        provider="marketaux",
        contract_version=_CONTRACT_VERSION,
    )
