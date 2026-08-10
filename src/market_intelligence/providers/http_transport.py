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
_FINNHUB_ENDPOINT: Final = "https://finnhub.io/api/v1/quote"
_EIA_ENDPOINT: Final = "https://api.eia.gov/v2/electricity/retail-sales/data/"
_SEC_ENDPOINT_PREFIX: Final = "https://data.sec.gov/submissions/CIK"
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
        credential = request.runtime_credential
        if credential is None:
            raise RuntimeError("provider_runtime_credential_missing")
        wire_params = dict(request.params)
        headers = {"Accept": "application/json"}
        if (
            request.provider == "marketaux"
            and request.operation == "news_all"
            and credential.name == "MARKETAUX_API_TOKEN"
        ):
            endpoint = _MARKETAUX_ENDPOINT
            wire_params["api_token"] = credential.reveal_for_transport()
        elif (
            request.provider == "finnhub"
            and request.operation == "quote"
            and credential.name == "FINNHUB_API_KEY"
        ):
            endpoint = _FINNHUB_ENDPOINT
            headers["X-Finnhub-Token"] = credential.reveal_for_transport()
        elif (
            request.provider == "eia"
            and request.operation == "electricity_retail_sales"
            and credential.name == "EIA_API_KEY"
        ):
            endpoint = _EIA_ENDPOINT
            wire_params["api_key"] = credential.reveal_for_transport()
        elif (
            request.provider == "sec_edgar"
            and request.operation == "submissions"
            and credential.name == "SEC_USER_AGENT"
        ):
            cik = wire_params.pop("cik", None)
            if not isinstance(cik, str) or not cik.isdigit() or len(cik) != 10:
                raise RuntimeError("provider_http_config_invalid")
            endpoint = f"{_SEC_ENDPOINT_PREFIX}{cik}.json"
            headers["User-Agent"] = credential.reveal_for_transport()
        else:
            raise RuntimeError("provider_http_operation_unsupported")
        try:
            if self._client is not None:
                response = await self._client.get(
                    endpoint,
                    params=wire_params,
                    headers=headers,
                    timeout=request.timeout_seconds,
                )
            else:
                async with httpx.AsyncClient() as client:
                    response = await client.get(
                        endpoint,
                        params=wire_params,
                        headers=headers,
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
