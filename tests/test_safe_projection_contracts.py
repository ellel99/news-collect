from __future__ import annotations

import pytest

from market_intelligence.safe_projection.contracts import (
    ProjectionContractError,
    canonical_projection_hash,
    validate_factual_payload,
)


@pytest.mark.parametrize(
    ("provider", "operation", "payload"),
    (
        (
            "marketaux",
            "news_all",
            {
                "provider_item_id": "article-1",
                "published_at": "2026-01-01T00:00:00+00:00",
                "title": "Safe title",
                "canonical_url": "https://example.com/article-1",
                "source_identity": "Example",
                "query": "technology",
                "language": "en",
                "symbols": ["NVDA"],
                "description_coverage": "blocked",
                "snippet_coverage": "blocked",
            },
        ),
        (
            "finnhub",
            "quote",
            {
                "provider_item_id": "AAPL:1",
                "published_at": "2026-01-01T00:00:00+00:00",
                "symbol": "AAPL",
                "provider_timestamp": 1,
                "c": 101.25,
                "d": 1.5,
                "dp": 1.5038,
                "h": 102.0,
                "l": 99.5,
                "o": 100.0,
                "pc": 99.75,
                "currency": "unknown",
                "exchange": "unknown",
            },
        ),
        (
            "eia",
            "electricity_retail_sales",
            {
                "provider_item_id": "2026-01:US:ALL",
                "published_at": "2026-01-01T00:00:00+00:00",
                "period": "2026-01",
                "dataset": "electricity",
                "series_identity": "electricity/retail-sales",
                "geography": "US",
                "sector": "ALL",
                "metric": "price",
                "value": 12.345,
                "unit": "cents_per_kwh",
            },
        ),
        (
            "sec_edgar",
            "submissions_recent",
            {
                "provider_item_id": "0000320193-26-000001",
                "published_at": "2026-01-01T00:00:00+00:00",
                "cik": "0000320193",
                "ticker": "AAPL",
                "accession_number": "0000320193-26-000001",
                "filing_date": "2026-01-01",
                "form": "8-K",
                "primary_document": "aapl-20260101.htm",
                "official_url": (
                    "https://www.sec.gov/Archives/edgar/data/320193/"
                    "000032019326000001/aapl-20260101.htm"
                ),
                "official_source": True,
            },
        ),
    ),
)
def test_v1_factual_payload_contracts_preserve_approved_values(
    provider: str, operation: str, payload: dict[str, object]
) -> None:
    validated = validate_factual_payload(provider, operation, 1, payload)
    assert validated == payload
    assert len(canonical_projection_hash(validated)) == 64


def test_numeric_values_are_not_replaced_with_placeholders() -> None:
    finnhub = validate_factual_payload(
        "finnhub",
        "quote",
        1,
        {
            "provider_item_id": "AAPL:1",
            "published_at": "2026-01-01T00:00:00+00:00",
            "symbol": "AAPL",
            "provider_timestamp": 1,
            "c": 101.25,
            "d": -2.5,
            "dp": -2.4096,
            "h": 104.0,
            "l": 100.0,
            "o": 103.5,
            "pc": 103.75,
            "currency": "unknown",
            "exchange": "unknown",
        },
    )
    assert finnhub["c"] == 101.25 and finnhub["dp"] == -2.4096


@pytest.mark.parametrize(
    ("provider", "operation", "payload", "error"),
    (
        ("unknown", "quote", {}, "projection_contract_unknown"),
        ("finnhub", "quote", {"token": "unsafe"}, "projection_secret_marker_detected"),
        (
            "sec_edgar",
            "submissions_recent",
            {
                "provider_item_id": "x",
                "published_at": "2026-01-01",
                "cik": "1",
                "ticker": "AAPL",
                "accession_number": "x",
                "filing_date": "2026-01-01",
                "form": "8-K",
                "primary_document": "file.htm",
                "official_url": "https://example.com/file.htm",
                "official_source": True,
            },
            "projection_sec_reference_invalid",
        ),
    ),
)
def test_unknown_unsafe_or_nonofficial_payload_fails_closed(
    provider: str, operation: str, payload: dict[str, object], error: str
) -> None:
    with pytest.raises(ProjectionContractError, match=error):
        validate_factual_payload(provider, operation, 1, payload)
