from __future__ import annotations

import enum
import re


class CollectionErrorCode(enum.StrEnum):
    TIMEOUT = "COLLECTION_TIMEOUT"
    NETWORK = "COLLECTION_NETWORK"
    RATE_LIMITED = "COLLECTION_RATE_LIMITED"
    UPSTREAM_5XX = "COLLECTION_UPSTREAM_5XX"
    UPSTREAM_RETRYABLE = "COLLECTION_UPSTREAM_RETRYABLE"
    DATABASE_UNAVAILABLE = "COLLECTION_DATABASE_UNAVAILABLE"
    LOCK_LOST = "COLLECTION_LOCK_LOST"
    AUTH = "COLLECTION_AUTH"
    FORBIDDEN = "COLLECTION_FORBIDDEN"
    NOT_FOUND = "COLLECTION_NOT_FOUND"
    CONTRACT_INVALID = "COLLECTION_CONTRACT_INVALID"
    CONFIG_INVALID = "COLLECTION_CONFIG_INVALID"
    CANCELLED = "COLLECTION_CANCELLED"
    STALE_RUN = "COLLECTION_STALE_RUN"
    UNKNOWN = "COLLECTION_UNKNOWN"


RETRYABLE_CODES = frozenset(
    {
        CollectionErrorCode.TIMEOUT,
        CollectionErrorCode.NETWORK,
        CollectionErrorCode.RATE_LIMITED,
        CollectionErrorCode.UPSTREAM_5XX,
        CollectionErrorCode.UPSTREAM_RETRYABLE,
        CollectionErrorCode.DATABASE_UNAVAILABLE,
        CollectionErrorCode.LOCK_LOST,
    }
)
_SENSITIVE = re.compile(
    r"(?i)(authorization|cookie|token|secret|password|cursor)\s*[:=]\s*\S+|https?://\S+"
)


def redact_detail(detail: str, limit: int = 500) -> str:
    return _SENSITIVE.sub("[REDACTED]", detail)[:limit]


class ClassifiedCollectionError(Exception):
    def __init__(
        self,
        code: CollectionErrorCode,
        detail: str,
        *,
        retry_after: float | None = None,
    ) -> None:
        self.code = code
        self.retryable = code in RETRYABLE_CODES
        self.retry_after = retry_after
        self.redacted_detail = redact_detail(detail)
        super().__init__(self.redacted_detail)
