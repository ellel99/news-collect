"""Bounded, operation-specific windows. Resolution is persisted once per run."""

import re
from datetime import UTC, datetime, timedelta
from typing import Any


def validate_window(operation: str, config: dict[str, Any]) -> None:
    mode = config.get("window_mode")
    monthly = operation == "electricity_retail_sales"
    ceiling = (366 if monthly else 7 if operation == "electricity_rto_region_data" else 31) * 86400
    if mode == "fixed_window":
        if set(config) & {
            "lookback_seconds",
            "overlap_seconds",
            "ingestion_lag_seconds",
            "granularity",
            "lookback_months",
            "overlap_months",
            "ingestion_lag_months",
        }:
            raise ValueError("breadth_window_invalid")
        try:
            if any(
                not isinstance(config.get(k), str)
                or not re.fullmatch(r"\d{4}-\d{2}-\d{2}(T\d{2})?", config[k])
                for k in ("start", "end")
            ):
                raise ValueError("breadth_window_invalid")
            start, end = (datetime.fromisoformat(config[k]) for k in ("start", "end"))
            seconds = (end - start).total_seconds()
        except (KeyError, ValueError, TypeError):
            raise ValueError("breadth_window_invalid") from None
        if not 0 < seconds <= ceiling:
            raise ValueError("breadth_window_invalid")
    elif mode == "rolling_window":
        if "start" in config or "end" in config:
            raise ValueError("breadth_window_invalid")
        if monthly:
            if set(config) & {"lookback_seconds", "overlap_seconds", "ingestion_lag_seconds"}:
                raise ValueError("breadth_window_invalid")
            months = [
                config.get(k) for k in ("lookback_months", "overlap_months", "ingestion_lag_months")
            ]
            if any(type(v) is not int for v in months):
                raise ValueError("breadth_window_invalid")
            lookback, overlap, lag = (int(v) for v in months if v is not None)
            if not (
                1 <= lookback <= 12
                and 0 <= overlap < lookback
                and 0 <= lag <= 12
                and config.get("granularity") == "month"
            ):
                raise ValueError("breadth_window_invalid")
            return
        if set(config) & {"lookback_months", "overlap_months", "ingestion_lag_months"}:
            raise ValueError("breadth_window_invalid")
        values = [
            config.get(k) for k in ("lookback_seconds", "overlap_seconds", "ingestion_lag_seconds")
        ]
        if any(type(v) is not int for v in values):
            raise ValueError("breadth_window_invalid")
        lookback, overlap, lag = (int(v) for v in values if v is not None)
        expected = (
            "month"
            if operation == "electricity_retail_sales"
            else "hour"
            if operation == "electricity_rto_region_data"
            else "day"
        )
        minimum = {"month": 31 * 86400, "day": 86400, "hour": 3600}[expected]
        if not (minimum <= lookback <= ceiling and 0 <= overlap < lookback and 0 <= lag <= ceiling):
            raise ValueError("breadth_window_invalid")
        if config.get("granularity") != expected:
            raise ValueError("breadth_window_invalid")
    else:
        raise ValueError("breadth_window_mode_required")


def resolve_window(
    operation: str, config: dict[str, Any], now: datetime, watermark: datetime | None = None
) -> dict[str, str]:
    validate_window(operation, config)
    if config["window_mode"] == "fixed_window":
        return {"start": config["start"], "end": config["end"]}
    if operation == "electricity_retail_sales":
        now = now.astimezone(UTC)
        end_month = now.year * 12 + now.month - 1 - config["ingestion_lag_months"]
        start_month = end_month - config["lookback_months"]
        if watermark is not None:
            watermark = watermark.astimezone(UTC)
            start_month = max(
                start_month,
                min(
                    watermark.year * 12 + watermark.month - 1 - config["overlap_months"],
                    end_month - 1,
                ),
            )

        def month_text(value: int) -> str:
            year, month = divmod(value, 12)
            return f"{year:04d}-{month + 1:02d}-01"

        return {"start": month_text(start_month), "end": month_text(end_month)}
    end = now.astimezone(UTC) - timedelta(seconds=config["ingestion_lag_seconds"])
    granularity = config["granularity"]
    end = end.replace(minute=0, second=0, microsecond=0)
    if granularity in {"day", "month"}:
        end = end.replace(hour=0)
    if granularity != "month":
        start = end - timedelta(seconds=config["lookback_seconds"])
        if watermark is not None:
            start = max(
                start,
                min(
                    watermark.astimezone(UTC) - timedelta(seconds=config["overlap_seconds"]),
                    end - timedelta(hours=1),
                ),
            )
        start = start.replace(minute=0, second=0, microsecond=0)
        if granularity == "day":
            start = start.replace(hour=0)
    fmt = "%Y-%m-%dT%H" if granularity == "hour" else "%Y-%m-%d"
    return {"start": start.strftime(fmt), "end": end.strftime(fmt)}


def resolved_config(config: dict[str, Any], window: dict[str, str]) -> dict[str, Any]:
    result = {
        k: v
        for k, v in config.items()
        if k
        not in {
            "lookback_seconds",
            "overlap_seconds",
            "ingestion_lag_seconds",
            "granularity",
            "lookback_months",
            "overlap_months",
            "ingestion_lag_months",
        }
    }
    return {**result, "window_mode": "fixed_window", **window}
