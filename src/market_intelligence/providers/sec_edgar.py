"""Bounded SEC EDGAR submissions metadata adapter."""

from __future__ import annotations

import json
from collections.abc import Mapping

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


class SecEdgarAdapter:
    provider_key = "sec_edgar"
    contract_version = 1

    def __init__(self, user_agent: RuntimeCredential | None) -> None:
        self._credential = user_agent

    async def fetch(
        self, request: ProviderFetchRequest, transport: ProviderTransport
    ) -> ProviderFetchResult:
        if self._credential is None or self._credential.name != "SEC_USER_AGENT":
            return failed(
                self.provider_key,
                ProviderAdapterErrorCode.CONFIG_INVALID,
                "provider_runtime_credential_missing",
                False,
            )
        ticker = safe_identifier(request.config.get("ticker"), max_length=12)
        cik = safe_identifier(request.config.get("cik"), max_length=10)
        if not 1 <= request.limit <= 10 or ticker is None or cik is None or not cik.isdigit():
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
                    operation="submissions",
                    params={"cik": cik.zfill(10)},
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
        recent = _recent_rows(response.body)
        if not recent:
            return failed(
                self.provider_key,
                ProviderAdapterErrorCode.CONTRACT_INVALID,
                "provider_response_shape_invalid",
                False,
            )
        raws, projections, displays, candidates = [], [], [], []
        for row in recent[: request.limit]:
            accession, filing_date, form = (
                safe_identifier(row.get("accessionNumber")),
                row.get("filingDate"),
                safe_identifier(row.get("form"), max_length=50),
            )
            published_at = iso_timestamp(filing_date)
            if (
                not all(isinstance(value, str) and value for value in (accession, form))
                or published_at is None
            ):
                return failed(
                    self.provider_key,
                    ProviderAdapterErrorCode.CONTRACT_INVALID,
                    "provider_item_identity_invalid",
                    False,
                )
            assert isinstance(accession, str) and isinstance(form, str)
            projection = {
                "provider_item_id": accession,
                "published_at": published_at,
                "field_names": safe_field_names(row),
                "ticker": ticker.upper(),
                "form": form,
                "has_primary_document": isinstance(row.get("primaryDocument"), str)
                and bool(row.get("primaryDocument")),
            }
            display = {
                "provider_item_id": accession,
                "published_at": published_at,
                "display_title": f"SEC {form} filing — {ticker.upper()}",
            }
            raws.append(
                raw_envelope(self.provider_key, accession, projection, response, "link_only")
            )
            projections.append(projection)
            displays.append(display)
            candidates.append((published_at, accession))
        published_at, item_id = max(candidates)
        return ProviderFetchResult(
            raw_items=tuple(raws),
            sanitized_metadata=tuple(projections),
            display_projections=tuple(displays),
            next_cursor=json.dumps(
                {"provider_item_id": item_id, "published_at": published_at},
                sort_keys=True,
                separators=(",", ":"),
            ),
            has_more=len(recent) > request.limit,
            safe_errors=(),
            provider=self.provider_key,
            contract_version=1,
        )


def _recent_rows(body: object) -> list[dict[str, object]]:
    if not isinstance(body, Mapping):
        return []
    filings = body.get("filings")
    recent = filings.get("recent") if isinstance(filings, Mapping) else None
    if not isinstance(recent, Mapping):
        return []
    keys = tuple(
        key for key, value in recent.items() if isinstance(key, str) and isinstance(value, list)
    )
    if not keys:
        return []
    size = min(len(recent[key]) for key in keys)
    return [{key: recent[key][index] for key in keys} for index in range(size)]
