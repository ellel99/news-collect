"""M2-A bounded operation adapters. Each fetch performs at most one transport request."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from typing import Any
from urllib.parse import urlsplit

from market_intelligence.providers.adapter_support import (
    failed as _failed,
)
from market_intelligence.providers.adapter_support import (
    iso_timestamp,
    raw_envelope,
    response_error,
)
from market_intelligence.providers.breadth_config import breadth_config
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
from market_intelligence.providers.marketaux import _retry_after, _sanitize_item
from market_intelligence.providers.sec_edgar import _recent_rows
from market_intelligence.safe_projection.contracts import (
    canonical_projection_hash,
    eia_series_identity,
    rto_series_identity,
    sec_official_url,
    validate_factual_payload,
)


def failed(
    provider: str, code: ProviderAdapterErrorCode, message: str, retryable: bool
) -> ProviderFetchResult:
    return replace(_failed(provider, code, message, retryable), contract_version=2)


class BreadthAdapter:
    contract_version = 2

    def __init__(self, provider: str, operation: str, credential: RuntimeCredential | None) -> None:
        self.provider_key, self.operation_key, self._credential = provider, operation, credential

    async def fetch(
        self, request: ProviderFetchRequest, transport: ProviderTransport
    ) -> ProviderFetchResult:
        try:
            credential_name = {
                "marketaux": "MARKETAUX_API_TOKEN",
                "finnhub": "FINNHUB_API_KEY",
                "eia": "EIA_API_KEY",
                "sec_edgar": "SEC_USER_AGENT",
            }.get(self.provider_key)
            if self._credential is None or self._credential.name != credential_name:
                raise ValueError("breadth_credential_missing")
            config = breadth_config(self.provider_key, self.operation_key, request.config)
            if config["window_mode"] != "fixed_window":
                raise ValueError("breadth_window_not_resolved")
            if not 1 <= request.limit <= 100:
                raise ValueError("breadth_limit_invalid")
            digest = canonical_projection_hash(config)
            state = dict(request.continuation or {})
            if state and (
                state.get("version") != 1
                or state.get("operation") != self.operation_key
                or state.get("config_hash") != digest
            ):
                raise ValueError("breadth_continuation_invalid")
            page, offset = state.get("page", 1), state.get("offset", 0)
            if (
                not isinstance(page, int)
                or not 1 <= page <= 1000
                or not isinstance(offset, int)
                or not 0 <= offset <= 100000
            ):
                raise ValueError("breadth_continuation_invalid")
            operation, params = self._params(config, request.limit, page, offset, state)
        except (ValueError, TypeError, KeyError):
            return failed(
                self.provider_key,
                ProviderAdapterErrorCode.CONFIG_INVALID,
                "breadth_config_invalid",
                False,
            )
        try:
            response = await transport.send(
                ProviderTransportRequest(
                    provider=self.provider_key,
                    operation=operation,
                    params=params,
                    timeout_seconds=request.request_timeout_seconds,
                    max_response_bytes=request.max_response_bytes,
                    runtime_credential=self._credential,
                )
            )
        except ProviderResponseTooLarge:
            return failed(
                self.provider_key,
                ProviderAdapterErrorCode.CONTRACT_INVALID,
                "provider_response_too_large",
                False,
            )
        except ProviderTransportTimeout:
            return failed(
                self.provider_key,
                ProviderAdapterErrorCode.TIMEOUT,
                "provider_request_timed_out",
                True,
            )
        except Exception:
            return failed(
                self.provider_key,
                ProviderAdapterErrorCode.UPSTREAM_ERROR,
                "breadth_transport_failed",
                True,
            )
        if error := response_error(self.provider_key, response.status_code):
            error_item = replace(
                error.safe_errors[0], retry_after_seconds=_retry_after(response.headers)
            )
            return replace(error, contract_version=2, safe_errors=(error_item,))
        try:
            rejected: list[str] = []
            payloads, more, progress = self._payloads(
                response.body, config, request.limit, page, offset, state, rejected
            )
            valid = []
            for payload in payloads:
                try:
                    valid.append(
                        validate_factual_payload(self.provider_key, self.operation_key, 1, payload)
                    )
                except ValueError:
                    identity = payload.get("provider_item_id")
                    if not isinstance(identity, str) or not re.fullmatch(
                        r"[A-Za-z0-9:_.-]{1,200}", identity
                    ):
                        raise ValueError("breadth_item_untraceable") from None
                    rejected.append(
                        hashlib.sha256(
                            f"{self.provider_key}:{self.operation_key}:{identity}".encode()
                        ).hexdigest()
                    )
            factuals = tuple(valid)
            metadata = tuple(
                {"provider_item_id": p["provider_item_id"], "published_at": p["published_at"]}
                for p in factuals
            )
            raws = tuple(
                raw_envelope(
                    self.provider_key,
                    p["provider_item_id"],
                    m,
                    response,
                    "link_only"
                    if self.provider_key in {"marketaux", "sec_edgar"}
                    else "metadata_only",
                )
                for p, m in zip(factuals, metadata, strict=True)
            )
            candidates = [json.loads(request.cursor)] if request.cursor else []
            candidates.extend(metadata)
            watermark = (
                max(candidates, key=lambda c: (c["published_at"], c["provider_item_id"]))
                if candidates
                else None
            )
            continuation = (
                {"version": 1, "operation": self.operation_key, "config_hash": digest, **progress}
                if more
                else None
            )
            return ProviderFetchResult(
                raw_items=raws,
                sanitized_metadata=metadata,
                next_cursor=json.dumps(watermark, sort_keys=True) if watermark else request.cursor,
                has_more=more,
                safe_errors=(),
                provider=self.provider_key,
                contract_version=2,
                factual_projections=factuals,
                continuation=continuation,
                rejected_row_hashes=tuple(rejected),
            )
        except (ValueError, TypeError, KeyError, IndexError, OverflowError):
            return failed(
                self.provider_key,
                ProviderAdapterErrorCode.CONTRACT_INVALID,
                "breadth_response_invalid",
                False,
            )

    def _params(
        self, c: dict[str, Any], limit: int, page: int, offset: int, state: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        if self.provider_key == "marketaux":
            params = {
                "search": c["query"],
                "limit": limit,
                "page": page,
                "published_after": c["start"],
                "published_before": c["end"],
                "sort": "published_asc",
            }
            if c.get("language"):
                params["language"] = c["language"]
            if c.get("symbols"):
                params["symbols"] = ",".join(c["symbols"])
            return "news_all", params
        if self.provider_key == "finnhub":
            return "company_news", {"symbol": c["symbol"], "from": c["start"], "to": c["end"]}
        if self.provider_key == "eia":
            if self.operation_key == "electricity_retail_sales":
                params = {
                    "frequency": "monthly",
                    "data[]": "price",
                    "offset": offset,
                    "length": limit,
                    "start": c["start"][:7],
                    "end": c["end"][:7],
                    "sort[0][column]": "period",
                    "sort[0][direction]": "asc",
                }
                params.update({f"facets[stateid][{i}]": v for i, v in enumerate(c["geographies"])})
                params.update({f"facets[sectorid][{i}]": v for i, v in enumerate(c["sectors"])})
                return self.operation_key, params
            params = {
                "frequency": "hourly",
                "data[0]": "value",
                "start": c["start"],
                "end": c["end"],
                "offset": offset,
                "length": limit,
                "sort[0][column]": "period",
                "sort[0][direction]": "asc",
                "sort[1][column]": "respondent",
                "sort[1][direction]": "asc",
                "sort[2][column]": "type",
                "sort[2][direction]": "asc",
            }
            params.update({f"facets[respondent][{i}]": r for i, r in enumerate(c["regions"])})
            params.update({f"facets[type][{i}]": t for i, t in enumerate(c["types"])})
            return "electricity_rto_region_data", params
        filename = state.get("file")
        if filename is not None:
            self._file(c["cik"], filename)
            return "submissions_history", {"file": filename}
        return "submissions", {"cik": c["cik"]}

    @staticmethod
    def _file(cik: str, name: object) -> str:
        if not isinstance(name, str) or not re.fullmatch(
            rf"CIK{cik}-submissions-\d{{3}}\.json", name
        ):
            raise ValueError("sec_history_reference_invalid")
        return name

    def _payloads(
        self,
        body: Any,
        c: dict[str, Any],
        limit: int,
        page: int,
        offset: int,
        state: dict[str, Any],
        rejected: list[str],
    ) -> tuple[list[dict[str, Any]], bool, dict[str, Any]]:
        if self.provider_key == "marketaux":
            rows = body["data"]
            if not isinstance(rows, list) or len(rows) > limit:
                raise ValueError("breadth_response_invalid")
            payloads = []
            for row in rows:
                if not isinstance(row, dict):
                    raise ValueError("breadth_response_invalid")
                item = _sanitize_item(row)
                if item is None:
                    identity = row.get("uuid") if isinstance(row, dict) else None
                    if not isinstance(identity, str) or not re.fullmatch(
                        r"[A-Za-z0-9_-]{1,160}", identity
                    ):
                        raise ValueError("breadth_item_untraceable")
                    rejected.append(
                        hashlib.sha256(f"marketaux:news_all:{identity}".encode()).hexdigest()
                    )
                    continue
                payloads.append(
                    {
                        "provider_item_id": item["provider_item_id"],
                        "published_at": item["published_at"],
                        "title": item["display_title"],
                        "canonical_url": item["display_url"],
                        "source_identity": item["source_identity"],
                        "query": c["query"],
                        "language": c.get("language"),
                        "symbols": c.get("symbols"),
                        "description_coverage": "blocked",
                        "snippet_coverage": "blocked",
                    }
                )
            found = body.get("meta", {}).get("found")
            more = bool(rows) and (
                page * limit < found if isinstance(found, int) else len(rows) == limit
            )
            return payloads, more, {"page": page + 1}
        if self.provider_key == "finnhub":
            if not isinstance(body, list):
                raise ValueError("breadth_response_invalid")
            payloads = []
            for row in body:
                if not isinstance(row, dict):
                    raise ValueError("breadth_response_invalid")
                published = iso_timestamp(row["datetime"])
                if published is None or not c["start"] <= published[:10] <= c["end"]:
                    continue
                identity = row.get("id")
                if identity is not None and (
                    isinstance(identity, bool)
                    or not isinstance(identity, (int, str))
                    or not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", str(identity))
                ):
                    raise ValueError("breadth_item_untraceable")
                if identity is None:
                    url = row.get("url")
                    if not isinstance(url, str):
                        raise ValueError("breadth_item_untraceable")
                    parsed = urlsplit(url)
                    if (
                        parsed.scheme not in {"http", "https"}
                        or not parsed.hostname
                        or parsed.username
                        or parsed.password
                        or re.search(r"(?i)token|api[_-]?key|authorization|secret", url)
                    ):
                        raise ValueError("breadth_item_untraceable")
                    identity = hashlib.sha256(
                        json.dumps([c["symbol"], published, url], sort_keys=True).encode()
                    ).hexdigest()
                payloads.append(
                    {
                        "provider_item_id": f"company-news:{c['symbol']}:{identity}",
                        "published_at": published,
                        "title": row.get("headline"),
                        "canonical_url": row.get("url"),
                        "source_identity": row.get("source"),
                        "symbol": c["symbol"],
                        "category": row.get("category"),
                        "summary_coverage": "blocked",
                    }
                )
            payloads.sort(key=lambda p: (p["published_at"], p["provider_item_id"]))
            last_key = self._key(state)
            payloads = [
                p for p in payloads if (p["published_at"], p["provider_item_id"]) > last_key
            ]
            emitted = payloads[:limit]
            return (
                emitted,
                len(payloads) > limit,
                {"last_key": [emitted[-1]["published_at"], emitted[-1]["provider_item_id"]]}
                if emitted
                else {},
            )
        if self.provider_key == "eia":
            rows = body["response"]["data"]
            if not isinstance(rows, list) or len(rows) > limit:
                raise ValueError("breadth_response_invalid")
            payloads = []
            for row in rows:
                if self.operation_key == "electricity_retail_sales":
                    geography, sector, period = row["stateid"], row["sectorid"], row["period"]
                    if (
                        geography not in c["geographies"]
                        or sector not in c["sectors"]
                        or not c["start"][:7] <= period <= c["end"][:7]
                    ):
                        raise ValueError("breadth_scope_invalid")
                    value = row["price"]
                    if isinstance(value, str):
                        value = float(value)
                    payloads.append(
                        {
                            "provider_item_id": f"{period}:{geography}:{sector}",
                            "published_at": iso_timestamp(period),
                            "period": period,
                            "dataset": "electricity",
                            "series_identity": eia_series_identity(geography, sector),
                            "geography": geography,
                            "sector": sector,
                            "metric": "price",
                            "value": value,
                            "unit": row.get("price-units") or "unknown",
                        }
                    )
                    continue
                region, metric, period = row["respondent"], row["type"], row["period"]
                if (
                    region not in c["regions"]
                    or metric not in c["types"]
                    or not c["start"] <= period <= c["end"]
                ):
                    raise ValueError("breadth_scope_invalid")
                series = rto_series_identity(region, metric)
                value = row["value"]
                if isinstance(value, str):
                    value = float(value)
                payloads.append(
                    {
                        "provider_item_id": "rto:"
                        + hashlib.sha256(f"{series}:{period}".encode()).hexdigest(),
                        "published_at": period + ":00:00+00:00",
                        "period": period,
                        "dataset": self.operation_key,
                        "series_identity": series,
                        "region": region,
                        "metric": metric,
                        "value": value,
                        "unit": row.get("value-units") or "unknown",
                    }
                )
            total = int(body["response"].get("total", offset + len(rows)))
            return (
                payloads,
                bool(rows) and offset + len(rows) < total,
                {"offset": offset + len(rows)},
            )
        rows = (
            _recent_rows({"filings": {"recent": body}}) if state.get("file") else _recent_rows(body)
        )
        files = list(state.get("files", []))
        if len(files) > c["max_history_files"]:
            raise ValueError("breadth_history_budget_exceeded")
        for name in files:
            self._file(c["cik"], name)
        if not state:
            references = body.get("filings", {}).get("files", [])
            for reference in references:
                name = self._file(c["cik"], reference["name"])
                if (
                    reference.get("filingTo", "") >= c["start"]
                    and reference.get("filingFrom", "9999") <= c["end"]
                ):
                    files.append(name)
            if len(files) > c["max_history_files"]:
                raise ValueError("breadth_history_budget_exceeded")
        eligible = [
            r
            for r in rows
            if r.get("form") in c["forms"] and c["start"] <= r.get("filingDate", "") <= c["end"]
        ]
        eligible.sort(key=lambda r: (r["filingDate"], r["accessionNumber"]))
        last_key = self._key(state)
        eligible = [r for r in eligible if (r["filingDate"], r["accessionNumber"]) > last_key]
        payloads = []
        filename = state.get("file", f"CIK{c['cik']}.json")
        for row in eligible[:limit]:
            accession, document = row["accessionNumber"], row["primaryDocument"]
            payloads.append(
                {
                    "provider_item_id": accession,
                    "published_at": iso_timestamp(row["filingDate"]),
                    "cik": c["cik"],
                    "ticker": c["ticker"],
                    "accession_number": accession,
                    "filing_date": row["filingDate"],
                    "form": row["form"],
                    "primary_document": document,
                    "official_url": sec_official_url(c["cik"], str(accession), str(document)),
                    "official_source": True,
                    "submissions_file": filename,
                }
            )
        if limit < len(eligible):
            return (
                payloads,
                True,
                {
                    "last_key": [
                        eligible[limit - 1]["filingDate"],
                        eligible[limit - 1]["accessionNumber"],
                    ],
                    "file": state.get("file"),
                    "files": files,
                },
            )
        if files:
            return payloads, True, {"file": files[0], "files": files[1:]}
        return payloads, False, {}

    @staticmethod
    def _key(state: dict[str, Any]) -> tuple[str, str]:
        key = state.get("last_key", ["", ""])
        if (
            not isinstance(key, list)
            or len(key) != 2
            or any(not isinstance(v, str) or len(v) > 200 for v in key)
        ):
            raise ValueError("breadth_continuation_invalid")
        return key[0], key[1]
