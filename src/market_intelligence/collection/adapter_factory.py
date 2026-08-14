"""Explicit, no-fallback adapter factory for approved provider operations."""

from market_intelligence.providers.eia import EiaAdapter
from market_intelligence.providers.finnhub import FinnhubAdapter
from market_intelligence.providers.marketaux_real import MarketauxRealAdapter
from market_intelligence.providers.sec_edgar import SecEdgarAdapter


class UnifiedAdapterFactory:
    def build(self, provider: str, operation_key: str, credential):  # type: ignore[no-untyped-def]
        factories = {
            ("marketaux", "news_all"): MarketauxRealAdapter,
            ("finnhub", "quote"): FinnhubAdapter,
            ("eia", "electricity_retail_sales"): EiaAdapter,
            ("sec_edgar", "submissions_recent"): SecEdgarAdapter,
        }
        factory = factories.get((provider, operation_key))
        if factory is None:
            raise LookupError("provider_operation_not_allowlisted")
        return factory(credential)
