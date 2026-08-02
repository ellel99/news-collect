"""Fail-closed registry for explicitly constructed provider adapters."""

from market_intelligence.providers.contracts import ProviderAdapter


class ProviderAdapterNotRegistered(LookupError):
    """Raised without echoing an unknown provider key."""


class ProviderAdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, ProviderAdapter] = {}

    def register(self, provider_key: str, adapter: ProviderAdapter) -> None:
        if not provider_key or provider_key != adapter.provider_key:
            raise ValueError("provider_adapter_key_mismatch")
        if provider_key in self._adapters:
            raise ValueError("provider_adapter_already_registered")
        self._adapters[provider_key] = adapter

    def get(self, provider_key: str) -> ProviderAdapter:
        adapter = self._adapters.get(provider_key)
        if adapter is None:
            raise ProviderAdapterNotRegistered("provider_adapter_unregistered")
        return adapter

    def supports(self, provider_key: str) -> bool:
        return provider_key in self._adapters
