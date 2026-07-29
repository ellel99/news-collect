from market_intelligence.collection.contracts import CollectionAdapter
from market_intelligence.collection.errors import ClassifiedCollectionError, CollectionErrorCode
from market_intelligence.collection.fake import FakeCollectionAdapter


class AdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, CollectionAdapter] = {}

    def register(self, access_method: str, adapter: CollectionAdapter) -> None:
        if access_method != "fake":
            raise ValueError("SPEC-0003 permits only the fake adapter")
        self._adapters[access_method] = adapter

    def resolve(self, access_method: str) -> CollectionAdapter:
        adapter = self._adapters.get(access_method)
        if adapter is None:
            raise ClassifiedCollectionError(
                CollectionErrorCode.CONFIG_INVALID,
                f"unregistered access method: {access_method}",
            )
        return adapter

    def supports(self, access_method: str) -> bool:
        return access_method == "fake" and access_method in self._adapters


def build_fake_registry() -> AdapterRegistry:
    registry = AdapterRegistry()
    registry.register("fake", FakeCollectionAdapter())
    return registry
