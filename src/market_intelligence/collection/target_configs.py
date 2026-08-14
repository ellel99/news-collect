"""Static, versioned R1 collection operation registry."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from market_intelligence.db.models import CollectionCursorStrategy, CollectionMode

_SECRET = re.compile(r"(?i)(api[_-]?key|api[_-]?token|authorization|password|secret|token)")
_URL = re.compile(r"(?i)https?://")


class TargetConfigError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class OperationContract:
    provider: str
    operation_key: str
    operation_config_version: int
    provider_contract_version: int
    cursor_strategy: CollectionCursorStrategy
    collection_mode: CollectionMode
    batch_ceiling: int
    credential_names: tuple[str, ...]
    legacy_cursor_type: str | None
    pagination_capability: str
    validator: Callable[[Mapping[str, Any]], Mapping[str, Any]]


def _keys(config: Mapping[str, Any], allowed: frozenset[str]) -> None:
    if not isinstance(config, Mapping) or set(config) - allowed:
        raise TargetConfigError("operation_config_invalid")
    _reject_unsafe(config)


def _reject_unsafe(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if _SECRET.search(str(key)) or str(key).lower() in {
                "url",
                "endpoint",
                "class",
                "module",
                "import",
            }:
                raise TargetConfigError("operation_config_unsafe")
            _reject_unsafe(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _reject_unsafe(child)
    elif isinstance(value, str) and (_SECRET.search(value) or _URL.search(value)):
        raise TargetConfigError("operation_config_unsafe")


def _marketaux(config: Mapping[str, Any]) -> Mapping[str, Any]:
    _keys(config, frozenset({"query", "language", "symbols"}))
    query = config.get("query")
    if not isinstance(query, str) or not 1 <= len(query) <= 200:
        raise TargetConfigError("operation_config_invalid")
    language = config.get("language")
    if language is not None and (
        not isinstance(language, str) or re.fullmatch(r"[a-z]{2}", language) is None
    ):
        raise TargetConfigError("operation_config_invalid")
    symbols = config.get("symbols")
    if symbols is not None and (
        not isinstance(symbols, list)
        or not 1 <= len(symbols) <= 10
        or any(not isinstance(item, str) or not 1 <= len(item) <= 20 for item in symbols)
    ):
        raise TargetConfigError("operation_config_invalid")
    return MappingProxyType(dict(config))


def _finnhub(config: Mapping[str, Any]) -> Mapping[str, Any]:
    _keys(config, frozenset({"symbol"}))
    symbol = config.get("symbol")
    if not isinstance(symbol, str) or re.fullmatch(r"[A-Z0-9.:-]{1,20}", symbol) is None:
        raise TargetConfigError("operation_config_invalid")
    return MappingProxyType(dict(config))


def _eia(config: Mapping[str, Any]) -> Mapping[str, Any]:
    _keys(config, frozenset({"dataset"}))
    if config.get("dataset") != "electricity":
        raise TargetConfigError("operation_config_invalid")
    return MappingProxyType({"dataset": "electricity"})


def _sec(config: Mapping[str, Any]) -> Mapping[str, Any]:
    _keys(config, frozenset({"ticker", "cik"}))
    ticker, cik = config.get("ticker"), config.get("cik")
    if ticker is not None and (
        not isinstance(ticker, str) or re.fullmatch(r"[A-Z0-9.-]{1,12}", ticker) is None
    ):
        raise TargetConfigError("operation_config_invalid")
    if cik is not None and (not isinstance(cik, str) or re.fullmatch(r"[0-9]{1,10}", cik) is None):
        raise TargetConfigError("operation_config_invalid")
    if ticker is None or cik is None:
        raise TargetConfigError("operation_config_invalid")
    result = dict(config)
    if cik is not None:
        result["cik"] = cik.zfill(10)
    return MappingProxyType(result)


class OperationRegistry:
    def __init__(self, contracts: tuple[OperationContract, ...]) -> None:
        self._contracts = {
            (
                c.provider,
                c.operation_key,
                c.operation_config_version,
                c.provider_contract_version,
            ): c
            for c in contracts
        }

    def resolve(
        self, provider: str, operation: str, config_version: int, contract_version: int
    ) -> OperationContract:
        contract = self._contracts.get((provider, operation, config_version, contract_version))
        if contract is None:
            raise TargetConfigError("operation_contract_unknown")
        return contract

    def validate(
        self,
        contract: OperationContract,
        config: Mapping[str, Any],
        *,
        batch_limit: int,
        max_requests: int,
        max_pages: int,
    ) -> Mapping[str, Any]:
        if (
            batch_limit < 1
            or batch_limit > contract.batch_ceiling
            or max_requests != 1
            or max_pages != 1
        ):
            raise TargetConfigError("operation_budget_invalid")
        return contract.validator(config)


def build_operation_registry() -> OperationRegistry:
    return OperationRegistry(
        (
            OperationContract(
                "marketaux",
                "news_all",
                1,
                1,
                CollectionCursorStrategy.COMPOUND,
                CollectionMode.INCREMENTAL,
                3,
                ("MARKETAUX_API_TOKEN",),
                "provider_cursor_v1",
                "none",
                _marketaux,
            ),
            OperationContract(
                "finnhub",
                "quote",
                1,
                1,
                CollectionCursorStrategy.COMPOUND,
                CollectionMode.INCREMENTAL,
                1,
                ("FINNHUB_API_KEY",),
                "provider_cursor_v1",
                "none",
                _finnhub,
            ),
            OperationContract(
                "eia",
                "electricity_retail_sales",
                1,
                1,
                CollectionCursorStrategy.COMPOUND,
                CollectionMode.SNAPSHOT,
                5,
                ("EIA_API_KEY",),
                "provider_cursor_v1",
                "none",
                _eia,
            ),
            OperationContract(
                "sec_edgar",
                "submissions_recent",
                1,
                1,
                CollectionCursorStrategy.REVISION,
                CollectionMode.SNAPSHOT,
                10,
                ("SEC_USER_AGENT", "SEC_CONTACT_EMAIL"),
                "provider_cursor_v1",
                "none",
                _sec,
            ),
        )
    )
