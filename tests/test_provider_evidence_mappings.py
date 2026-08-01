from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import pytest

from market_intelligence.evidence import (
    ProcessingStatus,
    map_eia_energy_row_to_evidence,
    map_finnhub_quote_to_evidence,
    map_marketaux_news_to_evidence,
    map_sec_filing_to_evidence,
    validate_evidence_envelope,
)

OBSERVED_AT = datetime(2026, 8, 2, tzinfo=UTC)
FORBIDDEN = (
    "private headline",
    "private description",
    "https://publisher.invalid/private",
    "quote-secret-value",
    "eia-secret-value",
    "0000123456-26-000001",
    "private-filing.htm",
)


def _serialized(envelope: object) -> str:
    return repr(asdict(envelope))  # type: ignore[arg-type]


def _assert_content_free(envelope: object) -> None:
    serialized = _serialized(envelope)
    assert all(value not in serialized for value in FORBIDDEN)


def test_marketaux_mapping_is_valid_and_content_free() -> None:
    envelope = map_marketaux_news_to_evidence(
        {
            "uuid": "marketaux-private-id",
            "title": FORBIDDEN[0],
            "url": FORBIDDEN[2],
            "snippet": "private snippet",
            "description": FORBIDDEN[1],
            "published_at": "2026-08-02T10:00:00Z",
            "entities": [{"name": "private entity"}],
            "keywords": ["private keyword"],
        },
        {"observed_at": OBSERVED_AT},
    )
    assert validate_evidence_envelope(envelope) == []
    assert envelope.news_signal_flag is True
    assert envelope.content_presence.has_title is True
    assert envelope.content_presence.has_url is True
    assert envelope.content_presence.has_snippet is True
    assert envelope.content_presence.has_description is True
    _assert_content_free(envelope)


def test_finnhub_mapping_counts_numeric_fields_without_values() -> None:
    envelope = map_finnhub_quote_to_evidence(
        {"c": 1.0, "d": 2.0, "dp": 3.0, "h": 4.0, "t": 1785664800},
        {"observed_at": OBSERVED_AT, "symbol": "PRIVATE-SYMBOL"},
    )
    assert validate_evidence_envelope(envelope) == []
    assert envelope.market_data_flag is True
    assert envelope.numeric_presence.has_numeric_value is True
    assert envelope.numeric_presence.numeric_field_count == 4
    assert "PRIVATE-SYMBOL" not in _serialized(envelope)
    assert all(str(value) not in _serialized(envelope) for value in (1.0, 2.0, 3.0, 4.0))


def test_eia_mapping_allows_missing_numeric_value() -> None:
    envelope = map_eia_energy_row_to_evidence(
        {"period": "2026-07", "sectorid": "private-sector", "stateid": "private-state"},
        {"observed_at": OBSERVED_AT},
    )
    assert validate_evidence_envelope(envelope) == []
    assert envelope.official_source_flag is True
    assert envelope.numeric_presence.nullable_allowed is True
    assert envelope.numeric_presence.has_numeric_value is False
    assert envelope.numeric_presence.numeric_field_count == 0
    assert "private-sector" not in _serialized(envelope)
    assert "private-state" not in _serialized(envelope)


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        (
            {
                "acceptanceDateTime": "2026-08-02T10:30:00Z",
                "filingDate": "2026-08-01",
                "reportDate": "2026-07-31",
            },
            datetime(2026, 8, 2, 10, 30, tzinfo=UTC),
        ),
        (
            {"filingDate": "2026-08-01", "reportDate": "2026-07-31"},
            datetime(2026, 8, 1, tzinfo=UTC),
        ),
        ({"reportDate": "2026-07-31"}, datetime(2026, 7, 31, tzinfo=UTC)),
    ],
)
def test_sec_event_time_priority(item: dict[str, object], expected: datetime) -> None:
    item.update(
        {
            "accessionNumber": FORBIDDEN[5],
            "primaryDocument": FORBIDDEN[6],
            "form": "private-form",
        }
    )
    envelope = map_sec_filing_to_evidence(
        item, {"observed_at": OBSERVED_AT, "ticker": "PRIVATE-TICKER"}
    )
    assert validate_evidence_envelope(envelope) == []
    assert envelope.event_time == expected
    assert envelope.official_source_flag is True
    assert envelope.disclosure_flag is True
    assert envelope.raw_payload_reference is not None
    assert envelope.raw_payload_reference.startswith("internal://evidence/sec_edgar/")
    _assert_content_free(envelope)


def test_missing_provider_item_id_is_blocked_with_safe_error() -> None:
    envelope = map_marketaux_news_to_evidence(
        {"published_at": "2026-08-02T10:00:00Z"},
        {"observed_at": OBSERVED_AT},
    )
    assert envelope.processing_status is ProcessingStatus.BLOCKED
    assert [error.code for error in envelope.errors] == ["provider_item_id_missing"]
    assert validate_evidence_envelope(envelope) == []


def test_missing_event_time_is_not_inferred() -> None:
    envelope = map_eia_energy_row_to_evidence(
        {"period": "unparseable", "sectorid": "sector", "stateid": "state"},
        {"observed_at": OBSERVED_AT},
    )
    assert envelope.event_time is None
    assert [error.code for error in validate_evidence_envelope(envelope)] == ["event_time_missing"]


def test_mapper_source_has_no_io_or_service_dependencies() -> None:
    source = Path("src/market_intelligence/evidence/provider_mappings.py").read_text()
    forbidden = (
        "requests",
        "httpx",
        "urlopen",
        "sqlalchemy",
        "openai",
        "local_evaluation",
        "Path(",
        "open(",
    )
    assert all(token not in source for token in forbidden)
