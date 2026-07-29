"""Source-independent collection framework."""

from market_intelligence.collection.contracts import (
    CollectionAdapter,
    CollectionTarget,
    CursorSnapshot,
    FetchBatch,
    FetchRequest,
    RawItemEnvelope,
)
from market_intelligence.collection.errors import ClassifiedCollectionError, CollectionErrorCode
from market_intelligence.collection.registry import AdapterRegistry, build_fake_registry

__all__ = [
    "AdapterRegistry",
    "ClassifiedCollectionError",
    "CollectionAdapter",
    "CollectionErrorCode",
    "CollectionTarget",
    "CursorSnapshot",
    "FetchBatch",
    "FetchRequest",
    "RawItemEnvelope",
    "build_fake_registry",
]
