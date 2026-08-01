from __future__ import annotations

from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from market_intelligence.evidence.contracts import (
    AccessLevel,
    CommonEvidenceEnvelope,
    ContentPresence,
    EvidenceError,
    EvidenceFlags,
    EvidenceKind,
    NumericPresence,
    ProcessingStatus,
    Provider,
    ProviderItemType,
    SourceType,
    provider_item_type_to_evidence_kind,
    provider_item_type_to_flags,
    validate_evidence_envelope,
    validate_raw_payload_reference,
)

SECRET = "credential-must-not-appear"


def _envelope(
    item_type: ProviderItemType = ProviderItemType.MARKETAUX_NEWS,
) -> CommonEvidenceEnvelope:
    mapping = {
        ProviderItemType.MARKETAUX_NEWS: (
            Provider.MARKETAUX,
            EvidenceKind.NEWS,
            SourceType.NEWS,
            EvidenceFlags(news_signal_flag=True),
            NumericPresence(),
        ),
        ProviderItemType.FINNHUB_QUOTE: (
            Provider.FINNHUB,
            EvidenceKind.MARKET_DATA,
            SourceType.MARKET_DATA,
            EvidenceFlags(market_data_flag=True),
            NumericPresence(has_numeric_value=True, numeric_field_count=7),
        ),
        ProviderItemType.EIA_ENERGY_TIMESERIES: (
            Provider.EIA,
            EvidenceKind.ENERGY_OFFICIAL,
            SourceType.OFFICIAL_ENERGY,
            EvidenceFlags(official_source_flag=True),
            NumericPresence(
                has_numeric_value=False,
                numeric_field_count=0,
                nullable_allowed=True,
            ),
        ),
        ProviderItemType.SEC_FILING: (
            Provider.SEC_EDGAR,
            EvidenceKind.DISCLOSURE,
            SourceType.DISCLOSURE,
            EvidenceFlags(official_source_flag=True, disclosure_flag=True),
            NumericPresence(),
        ),
    }
    provider, kind, source_type, flags, numeric = mapping[item_type]
    return CommonEvidenceEnvelope(
        evidence_version=1,
        provider=provider,
        provider_item_type=item_type,
        source_type=source_type,
        source_priority=None,
        access_level=AccessLevel.LINK_ONLY,
        provider_item_id="provider-item-hash-reference",
        provider_item_hash="a" * 64,
        canonical_source_reference=None,
        observed_at=datetime(2026, 8, 2, tzinfo=UTC),
        event_time=datetime(2026, 8, 2, tzinfo=UTC),
        entity_refs=(),
        asset_refs=(),
        topic_refs=(),
        dedup_candidate_key="provider-scope-candidate",
        evidence_kind=kind,
        evidence_confidence=None,
        content_presence=ContentPresence(),
        numeric_presence=numeric,
        official_source_flag=flags.official_source_flag,
        market_data_flag=flags.market_data_flag,
        disclosure_flag=flags.disclosure_flag,
        news_signal_flag=flags.news_signal_flag,
        raw_payload_reference="capture://safe-hash-reference",
        processing_status=ProcessingStatus.PENDING,
        errors=(),
    )


@pytest.mark.parametrize(
    ("item_type", "kind", "flags"),
    [
        (
            ProviderItemType.MARKETAUX_NEWS,
            EvidenceKind.NEWS,
            EvidenceFlags(news_signal_flag=True),
        ),
        (
            ProviderItemType.FINNHUB_QUOTE,
            EvidenceKind.MARKET_DATA,
            EvidenceFlags(market_data_flag=True),
        ),
        (
            ProviderItemType.EIA_ENERGY_TIMESERIES,
            EvidenceKind.ENERGY_OFFICIAL,
            EvidenceFlags(official_source_flag=True),
        ),
        (
            ProviderItemType.SEC_FILING,
            EvidenceKind.DISCLOSURE,
            EvidenceFlags(official_source_flag=True, disclosure_flag=True),
        ),
    ],
)
def test_provider_item_type_mappings(
    item_type: ProviderItemType,
    kind: EvidenceKind,
    flags: EvidenceFlags,
) -> None:
    assert provider_item_type_to_evidence_kind(item_type) is kind
    assert provider_item_type_to_flags(item_type) == flags
    assert validate_evidence_envelope(_envelope(item_type)) == []


def test_empty_entity_asset_and_topic_refs_are_valid() -> None:
    envelope = _envelope()
    assert envelope.entity_refs == ()
    assert envelope.asset_refs == ()
    assert envelope.topic_refs == ()
    assert validate_evidence_envelope(envelope) == []


def test_eia_nullable_numeric_presence_is_valid() -> None:
    envelope = _envelope(ProviderItemType.EIA_ENERGY_TIMESERIES)
    assert envelope.numeric_presence.nullable_allowed is True
    assert envelope.numeric_presence.has_numeric_value is False
    assert validate_evidence_envelope(envelope) == []


def _error_codes(envelope: CommonEvidenceEnvelope) -> set[str]:
    return {error.code for error in validate_evidence_envelope(envelope)}


def test_unknown_provider_fails_closed() -> None:
    assert "unknown_provider" in _error_codes(replace(_envelope(), provider="unknown"))


def test_unknown_provider_item_type_fails_closed() -> None:
    envelope = replace(_envelope(), provider_item_type="unknown")
    assert "unknown_provider_item_type" in _error_codes(envelope)
    with pytest.raises(ValueError, match="unknown_provider_item_type"):
        provider_item_type_to_evidence_kind("unknown")


def test_unknown_evidence_kind_fails_closed() -> None:
    assert "unknown_evidence_kind" in _error_codes(replace(_envelope(), evidence_kind="unknown"))


def test_unknown_access_level_is_blocked_by_validation() -> None:
    codes = _error_codes(replace(_envelope(), access_level=AccessLevel.UNKNOWN))
    assert "access_level_unknown" in codes


@pytest.mark.parametrize("marker", ("api_key=", "api_token=", "token="))
def test_raw_payload_reference_secret_markers_fail_closed(marker: str) -> None:
    reference = f"capture://safe?{marker}{SECRET}"
    errors = validate_raw_payload_reference(reference)
    assert [error.code for error in errors] == ["unsafe_raw_payload_reference"]
    assert SECRET not in repr(errors)
    assert reference not in repr(errors)


def test_external_raw_payload_reference_fails_closed() -> None:
    errors = validate_raw_payload_reference("https://example.invalid/raw-reference")
    assert [error.code for error in errors] == ["unsafe_raw_payload_reference"]


def test_missing_event_time_is_safe_error_and_not_inferred() -> None:
    envelope = replace(_envelope(), event_time=None)
    errors = validate_evidence_envelope(envelope)
    assert [error.code for error in errors] == ["event_time_missing"]
    assert envelope.event_time is None


def test_invalid_hash_is_reported_without_dedup() -> None:
    errors = validate_evidence_envelope(replace(_envelope(), provider_item_hash=None))
    assert [error.code for error in errors] == ["provider_item_hash_invalid"]


def test_unsafe_embedded_error_is_not_echoed() -> None:
    unsafe = EvidenceError(
        code="unsafe",
        field="errors",
        safe_message=f"private input {SECRET}",
    )
    errors = validate_evidence_envelope(replace(_envelope(), errors=(unsafe,)))
    assert [error.code for error in errors] == ["unsafe_embedded_error"]
    assert SECRET not in repr(errors)


def test_validation_errors_only_contain_safe_codes_and_messages() -> None:
    envelope = replace(
        _envelope(),
        provider="unknown",
        provider_item_type="unknown",
        evidence_kind="unknown",
        access_level=AccessLevel.UNKNOWN,
        provider_item_hash=None,
        event_time=None,
        raw_payload_reference=f"capture://safe?api_key={SECRET}",
    )
    serialized = repr([asdict(error) for error in validate_evidence_envelope(envelope)])
    assert SECRET not in serialized
    assert "api_key=" not in serialized
    for error in validate_evidence_envelope(envelope):
        assert error.code.replace("_", "").isalnum()
        assert "http" not in error.safe_message.lower()


def test_contract_module_has_no_io_network_database_ai_or_local_capture_dependency() -> None:
    source = (
        Path(__file__).parents[1] / "src/market_intelligence/evidence/contracts.py"
    ).read_text(encoding="utf-8")
    for forbidden in (
        "import httpx",
        "import requests",
        "urlopen",
        "sqlalchemy",
        "AsyncSession",
        "openai",
        "local_evaluation",
        "provider_capture",
        "Path(",
        "open(",
    ):
        assert forbidden not in source


def test_models_do_not_contain_content_or_numeric_value_fields() -> None:
    envelope_fields = CommonEvidenceEnvelope.__dataclass_fields__
    forbidden_fields = {
        "title",
        "body",
        "url",
        "snippet",
        "description",
        "quote_value",
        "eia_value",
        "accession_number",
        "primary_document",
        "raw_payload",
    }
    assert forbidden_fields.isdisjoint(envelope_fields)
