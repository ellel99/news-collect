"""M2-A allowlisted, bounded operation configuration; no environment or I/O."""

import re
from collections.abc import Mapping
from typing import Any

from market_intelligence.providers.windows import validate_window

FORMS = frozenset({"8-K", "10-Q", "10-K", "6-K"})


def breadth_config(provider: str, operation: str, config: Mapping[str, Any]) -> dict[str, Any]:
    keys = {
        ("marketaux", "news_all"): {"query", "language", "symbols", "start", "end"},
        ("finnhub", "company_news"): {"symbol", "start", "end"},
        ("eia", "electricity_retail_sales"): {
            "geographies",
            "sectors",
            "frequency",
            "start",
            "end",
        },
        ("eia", "electricity_rto_region_data"): {"regions", "types", "frequency", "start", "end"},
        ("sec_edgar", "submissions_recent"): {
            "ticker",
            "cik",
            "forms",
            "start",
            "end",
            "max_history_files",
        },
    }.get((provider, operation))
    window_keys = {
        "window_mode",
        "lookback_seconds",
        "overlap_seconds",
        "ingestion_lag_seconds",
        "granularity",
        "lookback_months",
        "overlap_months",
        "ingestion_lag_months",
    }
    if keys is None or set(config) - (keys | window_keys):
        raise ValueError("breadth_operation_config_invalid")
    result = dict(config)
    validate_window(operation, result)
    if provider in {"finnhub", "sec_edgar"}:
        for name in ("symbol",) if provider == "finnhub" else ("ticker",):
            if not isinstance(result.get(name), str) or not re.fullmatch(
                r"[A-Z][A-Z0-9.-]{0,19}", result[name]
            ):
                raise ValueError("breadth_symbol_invalid")
    if provider == "marketaux":
        if not isinstance(result.get("query"), str) or not 1 <= len(result["query"]) <= 200:
            raise ValueError("breadth_query_invalid")
        if result.get("language") is not None and not re.fullmatch(
            r"[a-z]{2}", str(result["language"])
        ):
            raise ValueError("breadth_language_invalid")
        symbols = result.get("symbols", [])
        if (
            not isinstance(symbols, list)
            or len(symbols) > 10
            or any(
                not isinstance(s, str) or not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,19}", s)
                for s in symbols
            )
        ):
            raise ValueError("breadth_symbols_invalid")
    if provider == "eia" and operation == "electricity_retail_sales":
        if result.get("frequency") != "monthly":
            raise ValueError("breadth_frequency_invalid")
        for field in ("geographies", "sectors"):
            values = result.get(field)
            if (
                not isinstance(values, list)
                or not 1 <= len(values) <= 10
                or any(
                    not isinstance(v, str) or not re.fullmatch(r"[A-Za-z0-9-]{1,12}", v)
                    for v in values
                )
            ):
                raise ValueError("breadth_facet_invalid")
    if provider == "eia" and operation == "electricity_rto_region_data":
        regions, types = result.get("regions"), result.get("types")
        if (
            result.get("frequency") != "hourly"
            or not isinstance(regions, list)
            or not 1 <= len(regions) <= 10
        ):
            raise ValueError("breadth_region_invalid")
        if any(not isinstance(r, str) or not re.fullmatch(r"[A-Z0-9-]{1,12}", r) for r in regions):
            raise ValueError("breadth_region_invalid")
        if not isinstance(types, list) or not types or set(types) - {"D", "NG"}:
            raise ValueError("breadth_metric_invalid")
    if provider == "sec_edgar":
        if not isinstance(result.get("cik"), str) or not re.fullmatch(r"\d{1,10}", result["cik"]):
            raise ValueError("breadth_cik_invalid")
        result["cik"] = result["cik"].zfill(10)
        forms = result.get("forms")
        if not isinstance(forms, list) or not forms or set(forms) - FORMS:
            raise ValueError("breadth_form_invalid")
        files = result.get("max_history_files", 2)
        if not isinstance(files, int) or isinstance(files, bool) or not 0 <= files <= 5:
            raise ValueError("breadth_history_budget_invalid")
        result["max_history_files"] = files
    return result
