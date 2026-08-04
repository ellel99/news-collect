"""Production HTTP transport boundary; callers must inject credentials explicitly."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Final

import httpx

from market_intelligence.providers.contracts import (
    ProviderTransportRequest,
    ProviderTransportResponse,
    ProviderTransportTimeout,
)

_MARKETAUX_ENDPOINT: Final = "https://api.marketaux.com/v1/news/all"
_SAFE_RESPONSE_HEADERS = frozenset(
    {
        "content-type",
        "retry-after",
        "x-ratelimit-limit",
        "x-ratelimit-remaining",
        "x-usagelimit-limit",
        "x-usagelimit-remaining",
    }
)


class HttpxProviderTransport:
    """Send allowlisted provider operations without logging request data."""

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client

    async def send(self, request: ProviderTransportRequest) -> ProviderTransportResponse:
        if request.provider != "marketaux" or request.operation != "news_all":
            raise RuntimeError("provider_http_operation_unsupported")
        credential = request.runtime_credential
        if credential is None or credential.name != "MARKETAUX_API_TOKEN":
            raise RuntimeError("provider_runtime_credential_missing")

        # Marketaux officially requires api_token in the wire query. It remains
        # outside provider-neutral params and is never returned or logged.
        wire_params = dict(request.params)
        wire_params["api_token"] = credential.reveal_for_transport()
        try:
            if self._client is not None:
                response = await self._client.get(
                    _MARKETAUX_ENDPOINT,
                    params=wire_params,
                    headers={"Accept": "application/json"},
                    timeout=request.timeout_seconds,
                )
            else:
                async with httpx.AsyncClient() as client:
                    response = await client.get(
                        _MARKETAUX_ENDPOINT,
                        params=wire_params,
                        headers={"Accept": "application/json"},
                        timeout=request.timeout_seconds,
                    )
        except httpx.TimeoutException:
            raise ProviderTransportTimeout("provider_request_timed_out") from None
        except httpx.HTTPError:
            raise RuntimeError("provider_http_transport_failed") from None

        try:
            body: object = response.json()
        except ValueError:
            body = None
        return ProviderTransportResponse(
            status_code=response.status_code,
            received_at=datetime.now(UTC),
            body=body,
            headers=_safe_headers(response.headers),
        )


def _safe_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {
        name: value for name, value in headers.items() if name.lower() in _SAFE_RESPONSE_HEADERS
    }
