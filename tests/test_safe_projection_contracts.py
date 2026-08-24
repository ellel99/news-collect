from __future__ import annotations

import inspect

import pytest

from market_intelligence.safe_projection.contracts import (
    ProjectionContractError,
    canonical_projection_hash,
    eia_series_identity,
    normalize_and_classify_factual_payload,
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
                "provider_item_id": "AAPL:1767225600",
                "published_at": "2026-01-01T00:00:00+00:00",
                "symbol": "AAPL",
                "provider_timestamp": 1767225600,
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
                "series_identity": "electricity/retail-sales/us/all/price",
                "geography": "us",
                "sector": "all",
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
            "provider_item_id": "AAPL:1767225600",
            "published_at": "2026-01-01T00:00:00+00:00",
            "symbol": "AAPL",
            "provider_timestamp": 1767225600,
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


def test_quality_is_operation_specific_and_recomputed() -> None:
    marketaux = {
        "provider_item_id": "article-1",
        "published_at": "2026-01-01T01:00:00+01:00",
        "title": None,
        "canonical_url": "https://example.com/article-1",
        "source_identity": "Example",
        "query": "technology",
        "language": "en",
        "symbols": ["NVDA"],
        "description_coverage": "blocked",
        "snippet_coverage": "blocked",
    }
    normalized, quality = normalize_and_classify_factual_payload(
        "marketaux", "news_all", 1, marketaux
    )
    assert normalized["published_at"] == "2026-01-01T00:00:00+00:00"
    assert quality == "partial"

    finnhub = {
        "provider_item_id": "AAPL:1767225600",
        "published_at": "2026-01-01T00:00:00+00:00",
        "symbol": "AAPL",
        "provider_timestamp": 1767225600,
        "c": 1.0,
        "d": 1.0,
        "dp": 1.0,
        "h": 1.0,
        "l": 1.0,
        "o": 1.0,
        "pc": 1.0,
        "currency": "unknown",
        "exchange": "unknown",
    }
    assert normalize_and_classify_factual_payload("finnhub", "quote", 1, finnhub)[1] == ("partial")


def test_eia_series_identity_is_period_stable_and_facet_specific() -> None:
    assert eia_series_identity("US", "ALL") == "electricity/retail-sales/us/all/price"
    assert eia_series_identity("US", "ALL") == eia_series_identity("us", "all")
    assert eia_series_identity("CA", "ALL") != eia_series_identity("US", "ALL")
    assert eia_series_identity("US", "RES") != eia_series_identity("US", "ALL")


@pytest.mark.parametrize("period", ["2026-00", "2026-13", "2026-1", "not-a-month"])
def test_eia_rejects_invalid_period_or_inconsistent_series(period: str) -> None:
    payload = {
        "provider_item_id": "item",
        "published_at": "2026-01-01T00:00:00+00:00",
        "period": period,
        "dataset": "electricity",
        "series_identity": "electricity/retail-sales/us/all/price",
        "geography": "US",
        "sector": "ALL",
        "metric": "price",
        "value": 1.5,
        "unit": "unknown",
    }
    with pytest.raises(ProjectionContractError, match="projection_eia_period_invalid"):
        validate_factual_payload("eia", "electricity_retail_sales", 1, payload)

    payload["period"] = "2026-01"
    payload["series_identity"] = "electricity/retail-sales/us/other/price"
    with pytest.raises(ProjectionContractError, match="projection_eia_series_invalid"):
        validate_factual_payload("eia", "electricity_retail_sales", 1, payload)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("cik", "320193"),
        ("accession_number", "invalid"),
        ("primary_document", "../filing.htm"),
        ("primary_document", "folder\\filing.htm"),
        ("filing_date", "2026-02-30"),
        (
            "official_url",
            "https://www.sec.gov/Archives/edgar/data/320193/wrong/file.htm",
        ),
    ),
)
def test_sec_identity_and_official_url_must_be_exact(field: str, value: str) -> None:
    payload = {
        "provider_item_id": "0000320193-26-000001",
        "published_at": "2026-01-01T00:00:00+00:00",
        "cik": "0000320193",
        "ticker": "AAPL",
        "accession_number": "0000320193-26-000001",
        "filing_date": "2026-01-01",
        "form": "8-K",
        "primary_document": "aapl.htm",
        "official_url": (
            "https://www.sec.gov/Archives/edgar/data/320193/000032019326000001/aapl.htm"
        ),
        "official_source": True,
    }
    payload[field] = value
    with pytest.raises(ProjectionContractError, match="projection_sec_reference_invalid"):
        validate_factual_payload("sec_edgar", "submissions_recent", 1, payload)


@pytest.mark.parametrize(
    ("provider", "operation", "payload", "error"),
    (
        ("unknown", "quote", {}, "projection_contract_unknown"),
        ("finnhub", "quote", {"token": "unsafe"}, "projection_secret_marker_detected"),
        (
            "marketaux",
            "news_all",
            {
                "provider_item_id": "article-1",
                "published_at": "2026-01-01T00:00:00",
                "title": None,
                "canonical_url": None,
                "source_identity": None,
                "query": "technology",
                "language": None,
                "symbols": None,
                "description_coverage": "blocked",
                "snippet_coverage": "blocked",
            },
            "projection_timestamp_invalid",
        ),
        (
            "sec_edgar",
            "submissions_recent",
            {
                "provider_item_id": "x",
                "published_at": "2026-01-01T00:00:00+00:00",
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


def test_r2_worker_is_isolated_from_legacy_evidence_and_external_runtime() -> None:
    import market_intelligence.safe_projection.worker as worker
    import market_intelligence.tasks.safe_projection as task

    source = (inspect.getsource(worker) + inspect.getsource(task)).lower()
    forbidden = (
        "provider_mappings",
        "evidencewriteservice",
        "contentitem",
        "eventcandidate",
        "notification",
        "requests",
        "httpx",
        "telegram",
        "openai",
    )
    assert all(name not in source for name in forbidden)
