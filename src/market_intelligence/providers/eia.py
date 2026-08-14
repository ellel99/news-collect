"""Bounded EIA official electricity-data adapter."""

from __future__ import annotations

import json

from market_intelligence.providers.adapter_support import (
    failed,
    iso_timestamp,
    raw_envelope,
    response_error,
    safe_field_names,
    safe_identifier,
)
from market_intelligence.providers.contracts import (
    ProviderAdapterErrorCode,
    ProviderFetchRequest,
    ProviderFetchResult,
    ProviderResponseTooLarge,
    ProviderTransport,
    ProviderTransportRequest,
    ProviderTransportTimeout,
)
from market_intelligence.providers.credentials import RuntimeCredential


class EiaAdapter:
    provider_key = "eia"
    contract_version = 1

    def __init__(self, credential: RuntimeCredential | None) -> None:
        self._credential = credential

    async def fetch(
        self, request: ProviderFetchRequest, transport: ProviderTransport
    ) -> ProviderFetchResult:
        if self._credential is None or self._credential.name != "EIA_API_KEY":
            return failed(
                self.provider_key,
                ProviderAdapterErrorCode.CONFIG_INVALID,
                "provider_runtime_credential_missing",
                False,
            )
        if not 1 <= request.limit <= 5 or request.config.get("dataset") != "electricity":
            return failed(
                self.provider_key,
                ProviderAdapterErrorCode.CONFIG_INVALID,
                "provider_config_invalid",
                False,
            )
        try:
            response = await transport.send(
                ProviderTransportRequest(
                    provider=self.provider_key,
                    operation="electricity_retail_sales",
                    params={
                        "data[]": "price",
                        "frequency": "monthly",
                        "length": request.limit,
                        "sort[0][column]": "period",
                        "sort[0][direction]": "desc",
                    },
                    timeout_seconds=request.request_timeout_seconds,
                    max_response_bytes=request.max_response_bytes,
                    runtime_credential=self._credential,
                )
            )
        except ProviderTransportTimeout:
            return failed(
                self.provider_key,
                ProviderAdapterErrorCode.TIMEOUT,
                "provider_request_timed_out",
                True,
            )
        except ProviderResponseTooLarge:
            return failed(
                self.provider_key,
                ProviderAdapterErrorCode.CONTRACT_INVALID,
                "provider_response_too_large",
                False,
            )
        except Exception:
            return failed(
                self.provider_key,
                ProviderAdapterErrorCode.UPSTREAM_ERROR,
                "provider_transport_failed",
                True,
            )
        if error := response_error(self.provider_key, response.status_code):
            return error
        body = response.body
        rows = (
            body.get("response", {}).get("data")
            if isinstance(body, dict) and isinstance(body.get("response"), dict)
            else None
        )
        if not isinstance(rows, list) or not rows:
            return failed(
                self.provider_key,
                ProviderAdapterErrorCode.CONTRACT_INVALID,
                "provider_response_shape_invalid",
                False,
            )
        raw_items, projections, displays, cursor_candidates = [], [], [], []
        for row in rows[: request.limit]:
            if not isinstance(row, dict):
                return failed(
                    self.provider_key,
                    ProviderAdapterErrorCode.CONTRACT_INVALID,
                    "provider_response_shape_invalid",
                    False,
                )
            published_at = iso_timestamp(row.get("period"))
            geography = safe_identifier(row.get("stateid") or row.get("stateDescription"))
            sector = safe_identifier(row.get("sectorid") or row.get("sectorName"))
            if published_at is None or geography is None or sector is None:
                return failed(
                    self.provider_key,
                    ProviderAdapterErrorCode.CONTRACT_INVALID,
                    "provider_item_identity_invalid",
                    False,
                )
            item_id = f"{row.get('period')}:{geography}:{sector}"
            projection = {
                "provider_item_id": item_id,
                "published_at": published_at,
                "field_names": safe_field_names(row),
                "geography": geography,
                "sector": sector,
                "has_numeric_value": isinstance(row.get("price"), (int, float)),
            }
            raw_items.append(
                raw_envelope(self.provider_key, item_id, projection, response, "metadata_only")
            )
            projections.append(projection)
            displays.append(
                {
                    "provider_item_id": item_id,
                    "published_at": published_at,
                    "display_title": f"EIA electricity update — {row.get('period')}",
                }
            )
            cursor_candidates.append((published_at, item_id))
        published_at, item_id = max(cursor_candidates)
        return ProviderFetchResult(
            raw_items=tuple(raw_items),
            sanitized_metadata=tuple(projections),
            display_projections=tuple(displays),
            next_cursor=json.dumps(
                {"provider_item_id": item_id, "published_at": published_at},
                sort_keys=True,
                separators=(",", ":"),
            ),
            has_more=len(rows) > request.limit,
            safe_errors=(),
            provider=self.provider_key,
            contract_version=1,
        )
