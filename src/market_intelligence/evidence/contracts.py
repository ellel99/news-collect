"""Pydantic-free, IO-free normalized evidence contract scaffold."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

EVIDENCE_VERSION = 1
HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SAFE_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
SAFE_FIELD_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
SECRET_MARKERS = (
    "api_key=",
    "api_token=",
    "token=",
    "authorization",
    "x-finnhub-token",
)
SAFE_RAW_REFERENCE_PREFIXES = ("capture://", "internal://", "local-ref://")
SAFE_ERROR_MESSAGES = frozenset(
    {
        "Access level must be explicitly classified.",
        "Event time is unavailable; no value was inferred.",
        "Evidence contract value is unknown.",
        "Evidence flags do not match the provider item type.",
        "Evidence kind does not match the provider item type.",
        "Evidence version is not supported.",
        "Numeric field count cannot be negative.",
        "Provider does not match the provider item type.",
        "Provider item hash is missing or invalid.",
        "Raw payload reference is unsafe.",
        "Source type does not match the provider item type.",
        "The embedded error is not content-safe.",
        "This evidence type must allow nullable numeric values.",
    }
)


class Provider(StrEnum):
    MARKETAUX = "marketaux"
    FINNHUB = "finnhub"
    EIA = "eia"
    SEC_EDGAR = "sec_edgar"


class ProviderItemType(StrEnum):
    MARKETAUX_NEWS = "marketaux_news"
    FINNHUB_QUOTE = "finnhub_quote"
    EIA_ENERGY_TIMESERIES = "eia_energy_timeseries"
    SEC_FILING = "sec_filing"


class EvidenceKind(StrEnum):
    NEWS = "news"
    MARKET_DATA = "market_data"
    ENERGY_OFFICIAL = "energy_official"
    DISCLOSURE = "disclosure"


class SourceType(StrEnum):
    NEWS = "news"
    MARKET_DATA = "market_data"
    OFFICIAL_ENERGY = "official_energy"
    DISCLOSURE = "disclosure"


class AccessLevel(StrEnum):
    PUBLIC_FULLTEXT = "public_fulltext"
    PUBLIC_SUMMARY = "public_summary"
    SUBSCRIPTION_REQUIRED = "subscription_required"
    LICENSED = "licensed"
    LINK_ONLY = "link_only"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


class ProcessingStatus(StrEnum):
    PENDING = "pending"
    VALIDATED = "validated"
    BLOCKED = "blocked"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class ContentPresence:
    has_title: bool = False
    has_body: bool = False
    has_url: bool = False
    has_snippet: bool = False
    has_description: bool = False


@dataclass(frozen=True, slots=True)
class NumericPresence:
    has_numeric_value: bool = False
    numeric_field_count: int = 0
    nullable_allowed: bool = False


@dataclass(frozen=True, slots=True)
class EvidenceError:
    code: str
    field: str | None
    safe_message: str


@dataclass(frozen=True, slots=True)
class EvidenceFlags:
    official_source_flag: bool = False
    market_data_flag: bool = False
    disclosure_flag: bool = False
    news_signal_flag: bool = False


@dataclass(frozen=True, slots=True)
class CommonEvidenceEnvelope:
    evidence_version: int
    provider: Provider | str
    provider_item_type: ProviderItemType | str
    source_type: SourceType | str
    source_priority: int | None
    access_level: AccessLevel | str
    provider_item_id: str | None
    provider_item_hash: str | None
    canonical_source_reference: str | None
    observed_at: datetime
    event_time: datetime | None
    entity_refs: tuple[str, ...] = ()
    asset_refs: tuple[str, ...] = ()
    topic_refs: tuple[str, ...] = ()
    dedup_candidate_key: str | None = None
    evidence_kind: EvidenceKind | str = EvidenceKind.NEWS
    evidence_confidence: str | None = None
    content_presence: ContentPresence = field(default_factory=ContentPresence)
    numeric_presence: NumericPresence = field(default_factory=NumericPresence)
    official_source_flag: bool = False
    market_data_flag: bool = False
    disclosure_flag: bool = False
    news_signal_flag: bool = False
    raw_payload_reference: str | None = None
    processing_status: ProcessingStatus | str = ProcessingStatus.PENDING
    errors: tuple[EvidenceError, ...] = ()


_KIND_BY_ITEM_TYPE = {
    ProviderItemType.MARKETAUX_NEWS: EvidenceKind.NEWS,
    ProviderItemType.FINNHUB_QUOTE: EvidenceKind.MARKET_DATA,
    ProviderItemType.EIA_ENERGY_TIMESERIES: EvidenceKind.ENERGY_OFFICIAL,
    ProviderItemType.SEC_FILING: EvidenceKind.DISCLOSURE,
}
_PROVIDER_BY_ITEM_TYPE = {
    ProviderItemType.MARKETAUX_NEWS: Provider.MARKETAUX,
    ProviderItemType.FINNHUB_QUOTE: Provider.FINNHUB,
    ProviderItemType.EIA_ENERGY_TIMESERIES: Provider.EIA,
    ProviderItemType.SEC_FILING: Provider.SEC_EDGAR,
}
_SOURCE_BY_ITEM_TYPE = {
    ProviderItemType.MARKETAUX_NEWS: SourceType.NEWS,
    ProviderItemType.FINNHUB_QUOTE: SourceType.MARKET_DATA,
    ProviderItemType.EIA_ENERGY_TIMESERIES: SourceType.OFFICIAL_ENERGY,
    ProviderItemType.SEC_FILING: SourceType.DISCLOSURE,
}
_FLAGS_BY_ITEM_TYPE = {
    ProviderItemType.MARKETAUX_NEWS: EvidenceFlags(news_signal_flag=True),
    ProviderItemType.FINNHUB_QUOTE: EvidenceFlags(market_data_flag=True),
    ProviderItemType.EIA_ENERGY_TIMESERIES: EvidenceFlags(official_source_flag=True),
    ProviderItemType.SEC_FILING: EvidenceFlags(
        official_source_flag=True,
        disclosure_flag=True,
    ),
}


def _error(code: str, field_name: str | None, safe_message: str) -> EvidenceError:
    return EvidenceError(code=code, field=field_name, safe_message=safe_message)


def _coerce_item_type(value: ProviderItemType | str) -> ProviderItemType:
    try:
        return ProviderItemType(value)
    except ValueError as exc:
        raise ValueError("unknown_provider_item_type") from exc


def provider_item_type_to_evidence_kind(
    provider_item_type: ProviderItemType | str,
) -> EvidenceKind:
    return _KIND_BY_ITEM_TYPE[_coerce_item_type(provider_item_type)]


def provider_item_type_to_flags(
    provider_item_type: ProviderItemType | str,
) -> EvidenceFlags:
    return _FLAGS_BY_ITEM_TYPE[_coerce_item_type(provider_item_type)]


def validate_raw_payload_reference(reference: str | None) -> list[EvidenceError]:
    if reference is None:
        return []
    lowered = reference.lower()
    if (
        any(marker in lowered for marker in SECRET_MARKERS)
        or lowered.startswith(("http://", "https://"))
        or not lowered.startswith(SAFE_RAW_REFERENCE_PREFIXES)
    ):
        return [
            _error(
                "unsafe_raw_payload_reference",
                "raw_payload_reference",
                "Raw payload reference is unsafe.",
            )
        ]
    return []


def _validate_embedded_error(error: EvidenceError) -> bool:
    return (
        SAFE_CODE_PATTERN.fullmatch(error.code) is not None
        and (error.field is None or SAFE_FIELD_PATTERN.fullmatch(error.field) is not None)
        and error.safe_message in SAFE_ERROR_MESSAGES
    )


def _enum_error(
    value: object, enum_type: type[StrEnum], code: str, field_name: str
) -> EvidenceError | None:
    if not isinstance(value, str):
        return _error(code, field_name, "Evidence contract value is unknown.")
    try:
        enum_type(value)
    except (TypeError, ValueError):
        return _error(code, field_name, "Evidence contract value is unknown.")
    return None


def validate_evidence_envelope(envelope: CommonEvidenceEnvelope) -> list[EvidenceError]:
    errors: list[EvidenceError] = []
    if envelope.evidence_version != EVIDENCE_VERSION:
        errors.append(
            _error(
                "unsupported_evidence_version",
                "evidence_version",
                "Evidence version is not supported.",
            )
        )

    enum_checks = (
        (envelope.provider, Provider, "unknown_provider", "provider"),
        (
            envelope.provider_item_type,
            ProviderItemType,
            "unknown_provider_item_type",
            "provider_item_type",
        ),
        (envelope.evidence_kind, EvidenceKind, "unknown_evidence_kind", "evidence_kind"),
        (envelope.source_type, SourceType, "unknown_source_type", "source_type"),
        (envelope.access_level, AccessLevel, "unknown_access_level", "access_level"),
        (
            envelope.processing_status,
            ProcessingStatus,
            "unknown_processing_status",
            "processing_status",
        ),
    )
    for value, enum_type, code, field_name in enum_checks:
        enum_error = _enum_error(value, enum_type, code, field_name)
        if enum_error is not None:
            errors.append(enum_error)

    try:
        item_type = ProviderItemType(envelope.provider_item_type)
    except (TypeError, ValueError):
        item_type = None
    if item_type is not None:
        if envelope.provider != _PROVIDER_BY_ITEM_TYPE[item_type]:
            errors.append(
                _error(
                    "provider_item_type_mismatch",
                    "provider",
                    "Provider does not match the provider item type.",
                )
            )
        if envelope.evidence_kind != _KIND_BY_ITEM_TYPE[item_type]:
            errors.append(
                _error(
                    "evidence_kind_mismatch",
                    "evidence_kind",
                    "Evidence kind does not match the provider item type.",
                )
            )
        if envelope.source_type != _SOURCE_BY_ITEM_TYPE[item_type]:
            errors.append(
                _error(
                    "source_type_mismatch",
                    "source_type",
                    "Source type does not match the provider item type.",
                )
            )
        expected_flags = _FLAGS_BY_ITEM_TYPE[item_type]
        actual_flags = EvidenceFlags(
            official_source_flag=envelope.official_source_flag,
            market_data_flag=envelope.market_data_flag,
            disclosure_flag=envelope.disclosure_flag,
            news_signal_flag=envelope.news_signal_flag,
        )
        if actual_flags != expected_flags:
            errors.append(
                _error(
                    "evidence_flags_mismatch",
                    None,
                    "Evidence flags do not match the provider item type.",
                )
            )
        if (
            item_type is ProviderItemType.EIA_ENERGY_TIMESERIES
            and not envelope.numeric_presence.nullable_allowed
        ):
            errors.append(
                _error(
                    "eia_numeric_nullable_required",
                    "numeric_presence",
                    "This evidence type must allow nullable numeric values.",
                )
            )

    if envelope.access_level == AccessLevel.UNKNOWN:
        errors.append(
            _error(
                "access_level_unknown",
                "access_level",
                "Access level must be explicitly classified.",
            )
        )
    if (
        envelope.provider_item_hash is None
        or HASH_PATTERN.fullmatch(envelope.provider_item_hash) is None
    ):
        errors.append(
            _error(
                "provider_item_hash_invalid",
                "provider_item_hash",
                "Provider item hash is missing or invalid.",
            )
        )
    if envelope.event_time is None:
        errors.append(
            _error(
                "event_time_missing",
                "event_time",
                "Event time is unavailable; no value was inferred.",
            )
        )
    if envelope.numeric_presence.numeric_field_count < 0:
        errors.append(
            _error(
                "numeric_field_count_invalid",
                "numeric_presence",
                "Numeric field count cannot be negative.",
            )
        )
    errors.extend(validate_raw_payload_reference(envelope.raw_payload_reference))
    if any(not _validate_embedded_error(error) for error in envelope.errors):
        errors.append(
            _error(
                "unsafe_embedded_error",
                "errors",
                "The embedded error is not content-safe.",
            )
        )
    return errors
