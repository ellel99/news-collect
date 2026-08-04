"""Marketaux real request boundary with explicit credential injection."""

from __future__ import annotations

from market_intelligence.providers.contracts import (
    ProviderAdapterError,
    ProviderAdapterErrorCode,
    ProviderFetchRequest,
    ProviderFetchResult,
    ProviderTransport,
)
from market_intelligence.providers.credentials import RuntimeCredential
from market_intelligence.providers.marketaux import MarketauxAdapter


class MarketauxRealAdapter(MarketauxAdapter):
    """Production adapter boundary; actual execution depends on injected transport."""

    def __init__(self, credential: RuntimeCredential | None) -> None:
        super().__init__(credential)
        self._runtime_credential = credential

    async def fetch(
        self,
        request: ProviderFetchRequest,
        transport: ProviderTransport,
    ) -> ProviderFetchResult:
        if (
            self._runtime_credential is None
            or self._runtime_credential.name != "MARKETAUX_API_TOKEN"
        ):
            return ProviderFetchResult(
                raw_items=(),
                sanitized_metadata=(),
                next_cursor=None,
                has_more=False,
                safe_errors=(
                    ProviderAdapterError(
                        code=ProviderAdapterErrorCode.CONFIG_INVALID,
                        safe_message="provider_runtime_credential_missing",
                        retryable=False,
                    ),
                ),
                provider=self.provider_key,
                contract_version=self.contract_version,
            )
        return await super().fetch(request, transport)
