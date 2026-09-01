"""Exact M2-A operation continuation codecs; no provider I/O or secrets."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from market_intelligence.safe_projection.contracts import canonical_projection_hash

_HASH = re.compile(r"[0-9a-f]{64}")
_FILE = re.compile(r"CIK\d{10}-submissions-\d{3}\.json")
_OPERATIONS = {
    ("marketaux", "news_all"): frozenset({"page"}),
    ("finnhub", "company_news"): frozenset({"last_key"}),
    ("eia", "electricity_retail_sales"): frozenset({"offset"}),
    ("eia", "electricity_rto_region_data"): frozenset({"offset"}),
    ("sec_edgar", "submissions_recent"): frozenset({"file", "files", "last_key"}),
}
_BASE = frozenset(
    {"version", "provider", "operation", "config_hash", "resolved_window", "lineage", "state"}
)
_LINEAGE = frozenset(
    {
        "target_id",
        "config_revision",
        "operation_key",
        "operation_config_version",
        "provider_contract_version",
        "cursor_version",
        "run_mode",
    }
)


class ContinuationContractError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ContinuationLineage:
    target_id: UUID
    config_revision: int
    operation_key: str
    operation_config_version: int
    provider_contract_version: int
    cursor_version: int
    run_mode: str

    def mapping(self) -> dict[str, str | int]:
        return {
            "target_id": str(self.target_id),
            "config_revision": self.config_revision,
            "operation_key": self.operation_key,
            "operation_config_version": self.operation_config_version,
            "provider_contract_version": self.provider_contract_version,
            "cursor_version": self.cursor_version,
            "run_mode": self.run_mode,
        }


def request_lineage(request: Any, operation: str) -> ContinuationLineage:
    values = (
        request.target_id,
        request.config_revision,
        request.operation_config_version,
        request.provider_contract_version,
        request.cursor_version,
        request.run_mode,
    )
    if (
        not isinstance(values[0], UUID)
        or any(type(value) is not int or value < 1 for value in values[1:5])
        or values[5] not in {"normal", "backfill"}
    ):
        raise ContinuationContractError("continuation_lineage_missing")
    return ContinuationLineage(
        values[0], values[1], operation, values[2], values[3], values[4], values[5]
    )


def encode_continuation(
    provider: str,
    operation: str,
    config: Mapping[str, Any],
    lineage: ContinuationLineage,
    state: Mapping[str, Any],
) -> dict[str, Any]:
    if (provider, operation) not in _OPERATIONS:
        raise ContinuationContractError("continuation_operation_unknown")
    result = {
        "version": 1,
        "provider": provider,
        "operation": operation,
        "config_hash": canonical_projection_hash(dict(config)),
        "resolved_window": {"start": config.get("start"), "end": config.get("end")},
        "lineage": lineage.mapping(),
        "state": dict(state),
    }
    decode_continuation(result, provider, operation, config, lineage)
    return result


def decode_continuation(
    value: Mapping[str, Any],
    provider: str,
    operation: str,
    config: Mapping[str, Any],
    lineage: ContinuationLineage,
) -> dict[str, Any]:
    if set(value) != _BASE or (provider, operation) not in _OPERATIONS:
        raise ContinuationContractError("continuation_shape_invalid")
    if (
        value.get("version") != 1
        or value.get("provider") != provider
        or value.get("operation") != operation
    ):
        raise ContinuationContractError("continuation_identity_invalid")
    digest = value.get("config_hash")
    if (
        not isinstance(digest, str)
        or not _HASH.fullmatch(digest)
        or digest != canonical_projection_hash(dict(config))
    ):
        raise ContinuationContractError("continuation_config_invalid")
    window = value.get("resolved_window")
    if (
        not isinstance(window, Mapping)
        or set(window) != {"start", "end"}
        or dict(window) != {"start": config.get("start"), "end": config.get("end")}
    ):
        raise ContinuationContractError("continuation_window_invalid")
    bound = value.get("lineage")
    if not isinstance(bound, Mapping) or set(bound) != _LINEAGE or dict(bound) != lineage.mapping():
        raise ContinuationContractError("continuation_lineage_invalid")
    state = value.get("state")
    if not isinstance(state, Mapping) or set(state) - _OPERATIONS[provider, operation]:
        raise ContinuationContractError("continuation_state_invalid")
    result = dict(state)
    if not result:
        return result
    if operation == "news_all":
        if (
            set(result) != {"page"}
            or type(result["page"]) is not int
            or not 2 <= result["page"] <= 1000
        ):
            raise ContinuationContractError("continuation_page_invalid")
    elif operation in {"electricity_retail_sales", "electricity_rto_region_data"}:
        if (
            set(result) != {"offset"}
            or type(result["offset"]) is not int
            or not 1 <= result["offset"] <= 10_000_000
        ):
            raise ContinuationContractError("continuation_offset_invalid")
    elif operation == "company_news":
        if set(result) != {"last_key"}:
            raise ContinuationContractError("continuation_key_invalid")
        _key(result["last_key"])
    else:
        allowed = {
            frozenset({"file"}),
            frozenset({"file", "files"}),
            frozenset({"file", "files", "last_key"}),
        }
        if frozenset(result) not in allowed:
            raise ContinuationContractError("continuation_sec_invalid")
        current, files = result.get("file"), result.get("files", [])
        if current is not None and (not isinstance(current, str) or not _FILE.fullmatch(current)):
            raise ContinuationContractError("continuation_sec_invalid")
        if (
            not isinstance(files, list)
            or len(files) > 5
            or any(not isinstance(item, str) or not _FILE.fullmatch(item) for item in files)
        ):
            raise ContinuationContractError("continuation_sec_invalid")
        if len(set(files)) != len(files) or current in files:
            raise ContinuationContractError("continuation_sec_invalid")
        cik = config.get("cik")
        expected_prefix = f"CIK{cik}-submissions-"
        if not isinstance(cik, str) or any(
            not item.startswith(expected_prefix)
            for item in ([current] if current is not None else []) + files
        ):
            raise ContinuationContractError("continuation_sec_invalid")
        if "last_key" in result:
            _key(result["last_key"])
        if current is None and frozenset(result) != frozenset({"file", "files", "last_key"}):
            raise ContinuationContractError("continuation_sec_invalid")
    return result


def _key(value: object) -> tuple[str, str]:
    if (
        not isinstance(value, list)
        or len(value) != 2
        or any(not isinstance(item, str) or not item or len(item) > 255 for item in value)
    ):
        raise ContinuationContractError("continuation_key_invalid")
    return value[0], value[1]
