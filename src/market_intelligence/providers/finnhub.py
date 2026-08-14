"""Bounded Finnhub quote adapter with injected credentials and transport."""

from __future__ import annotations

from market_intelligence.providers.adapter_support import (
    failed,
    iso_timestamp,
    raw_envelope,
    response_error,
    safe_field_names,
    safe_identifier,
)
from market_intelligence.providers.contracts import (
    ProviderAdapterErrorCode,
    ProviderFetchRequest,
    ProviderFetchResult,
    ProviderResponseTooLarge,
    ProviderTransport,
    ProviderTransportRequest,
    ProviderTransportTimeout,
)
from market_intelligence.providers.credentials import RuntimeCredential

_QUOTE_FIELDS = ("c", "d", "dp", "h", "l", "o", "pc")


class FinnhubAdapter:
    provider_key = "finnhub"
    contract_version = 1

    def __init__(self, credential: RuntimeCredential | None) -> None:
        self._credential = credential

    async def fetch(
        self, request: ProviderFetchRequest, transport: ProviderTransport
    ) -> ProviderFetchResult:
        if self._credential is None or self._credential.name != "FINNHUB_API_KEY":
            return failed(
                self.provider_key,
                ProviderAdapterErrorCode.CONFIG_INVALID,
                "provider_runtime_credential_missing",
                False,
            )
        symbol = safe_identifier(request.config.get("symbol"), max_length=20)
        if request.limit != 1 or symbol is None:
            return failed(
                self.provider_key,
                ProviderAdapterErrorCode.CONFIG_INVALID,
                "provider_config_invalid",
                False,
            )
        try:
            response = await transport.send(
                ProviderTransportRequest(
                    provider=self.provider_key,
                    operation="quote",
                    params={"symbol": symbol.upper()},
                    timeout_seconds=request.request_timeout_seconds,
                    max_response_bytes=request.max_response_bytes,
                    runtime_credential=self._credential,
                )
            )
        except ProviderTransportTimeout:
            return failed(
                self.provider_key,
                ProviderAdapterErrorCode.TIMEOUT,
                "provider_request_timed_out",
                True,
            )
        except ProviderResponseTooLarge:
            return failed(
                self.provider_key,
                ProviderAdapterErrorCode.CONTRACT_INVALID,
                "provider_response_too_large",
                False,
            )
        except Exception:
            return failed(
                self.provider_key,
                ProviderAdapterErrorCode.UPSTREAM_ERROR,
                "provider_transport_failed",
                True,
            )
        if error := response_error(self.provider_key, response.status_code):
            return error
        if not isinstance(response.body, dict):
            return failed(
                self.provider_key,
                ProviderAdapterErrorCode.CONTRACT_INVALID,
                "provider_response_shape_invalid",
                False,
            )
        published_at = iso_timestamp(response.body.get("t"))
        numeric_count = sum(
            isinstance(response.body.get(key), (int, float)) for key in _QUOTE_FIELDS
        )
        if published_at is None or numeric_count == 0:
            return failed(
                self.provider_key,
                ProviderAdapterErrorCode.CONTRACT_INVALID,
                "provider_response_shape_invalid",
                False,
            )
        item_id = f"{symbol.upper()}:{response.body['t']}"
        projection = {
            "provider_item_id": item_id,
            "published_at": published_at,
            "field_names": safe_field_names(response.body),
            "symbol": symbol.upper(),
            "numeric_field_count": numeric_count,
        }
        raw = raw_envelope(self.provider_key, item_id, projection, response, "metadata_only")
        return ProviderFetchResult(
            raw_items=(raw,),
            sanitized_metadata=(projection,),
            display_projections=(
                {
                    "provider_item_id": item_id,
                    "published_at": published_at,
                    "display_title": f"Finnhub quote update — {symbol.upper()}",
                },
            ),
            next_cursor=_cursor(published_at, item_id),
            has_more=False,
            safe_errors=(),
            provider=self.provider_key,
            contract_version=1,
        )


def _cursor(published_at: str, item_id: str) -> str:
    import json

    return json.dumps(
        {"provider_item_id": item_id, "published_at": published_at},
        sort_keys=True,
        separators=(",", ":"),
    )
