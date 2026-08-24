"""Durable provider-neutral safe factual projection boundary."""

from market_intelligence.safe_projection.contracts import (
    ProjectionContractError,
    canonical_projection_hash,
    normalize_and_classify_factual_payload,
    validate_factual_payload,
)
from market_intelligence.safe_projection.worker import (
    ProjectionValidationReport,
    SafeFactProjectionWorker,
)

__all__ = [
    "ProjectionContractError",
    "ProjectionValidationReport",
    "SafeFactProjectionWorker",
    "canonical_projection_hash",
    "normalize_and_classify_factual_payload",
    "validate_factual_payload",
]
