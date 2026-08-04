"""Provider preflight utilities and network-free adapter scaffold contracts."""

from market_intelligence.providers.contracts import (
    ProviderAdapter,
    ProviderAdapterError,
    ProviderAdapterErrorCode,
    ProviderFetchRequest,
    ProviderFetchResult,
    ProviderTransport,
    ProviderTransportRequest,
    ProviderTransportResponse,
    ProviderTransportTimeout,
)
from market_intelligence.providers.credentials import RuntimeCredential
from market_intelligence.providers.http_transport import HttpxProviderTransport
from market_intelligence.providers.marketaux import MarketauxAdapter
from market_intelligence.providers.marketaux_real import MarketauxRealAdapter
from market_intelligence.providers.registry import (
    ProviderAdapterNotRegistered,
    ProviderAdapterRegistry,
)
from market_intelligence.providers.transport import MockProviderTransport

__all__ = [
    "HttpxProviderTransport",
    "MarketauxAdapter",
    "MarketauxRealAdapter",
    "MockProviderTransport",
    "ProviderAdapter",
    "ProviderAdapterError",
    "ProviderAdapterErrorCode",
    "ProviderAdapterNotRegistered",
    "ProviderAdapterRegistry",
    "ProviderFetchRequest",
    "ProviderFetchResult",
    "ProviderTransport",
    "ProviderTransportRequest",
    "ProviderTransportResponse",
    "ProviderTransportTimeout",
    "RuntimeCredential",
]
