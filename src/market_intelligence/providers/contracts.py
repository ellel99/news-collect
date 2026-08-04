"""Provider adapter contracts with no credential or persistence concerns."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Protocol
from uuid import UUID

from market_intelligence.collection.contracts import RawItemEnvelope
from market_intelligence.providers.credentials import RuntimeCredential

_SECRET_FIELD = frozenset(
    {"api_key", "api_token", "token", "authorization", "x-finnhub-token", "secret", "password"}
)
_SECRET_VALUE = re.compile(
    r"(?i)(api[_-]?key|api[_-]?token|authorization|x-finnhub-token|token|secret|password)="
)


def _immutable_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(dict(value))


class ProviderAdapterErrorCode(StrEnum):
    CONFIG_INVALID = "provider_config_invalid"
    CONTRACT_INVALID = "provider_contract_invalid"
    RATE_LIMITED = "provider_rate_limited"
    TIMEOUT = "provider_timeout"
    UPSTREAM_ERROR = "provider_upstream_error"


@dataclass(frozen=True, slots=True)
class ProviderAdapterError:
    code: ProviderAdapterErrorCode
    safe_message: str
    retryable: bool
    retry_after_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class ProviderFetchRequest:
    source_id: UUID
    source_account_id: UUID | None
    cursor: str | None
    config: Mapping[str, Any]
    limit: int
    deadline_at: datetime
    correlation_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "config", _immutable_mapping(self.config))


@dataclass(frozen=True, slots=True)
class ProviderTransportRequest:
    provider: str
    operation: str
    params: Mapping[str, str | int]
    timeout_seconds: float
    runtime_credential: RuntimeCredential | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if any(
            str(key).lower() in _SECRET_FIELD or _SECRET_VALUE.search(str(value))
            for key, value in self.params.items()
        ):
            raise ValueError("provider_transport_request_contains_secret")
        object.__setattr__(self, "params", _immutable_mapping(self.params))


@dataclass(frozen=True, slots=True)
class ProviderTransportResponse:
    status_code: int
    received_at: datetime
    body: object
    headers: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "headers", _immutable_mapping(self.headers))


@dataclass(frozen=True, slots=True)
class ProviderFetchResult:
    raw_items: tuple[RawItemEnvelope, ...]
    sanitized_metadata: tuple[Mapping[str, Any], ...]
    next_cursor: str | None
    has_more: bool
    safe_errors: tuple[ProviderAdapterError, ...]
    provider: str
    contract_version: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "sanitized_metadata",
            tuple(_immutable_mapping(item) for item in self.sanitized_metadata),
        )
        if len(self.raw_items) != len(self.sanitized_metadata):
            raise ValueError("provider_result_metadata_count_mismatch")


class ProviderTransportTimeout(TimeoutError):
    """Safe transport timeout signal without request details."""


class ProviderTransport(Protocol):
    async def send(self, request: ProviderTransportRequest) -> ProviderTransportResponse: ...


class ProviderAdapter(Protocol):
    provider_key: str
    contract_version: int

    async def fetch(
        self,
        request: ProviderFetchRequest,
        transport: ProviderTransport,
    ) -> ProviderFetchResult: ...
