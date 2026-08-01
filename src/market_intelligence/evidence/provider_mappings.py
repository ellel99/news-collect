"""Pure, content-free provider item mappings to evidence envelopes."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Final

from market_intelligence.evidence.contracts import (
    EVIDENCE_VERSION,
    AccessLevel,
    CommonEvidenceEnvelope,
    ContentPresence,
    EvidenceError,
    EvidenceKind,
    NumericPresence,
    ProcessingStatus,
    Provider,
    ProviderItemType,
    SourceType,
)

_NUMERIC_QUOTE_FIELDS: Final = ("c", "d", "dp", "h", "l", "o", "pc")


def _stable_hash(value: object) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _opaque_ref(namespace: str, value: object) -> str:
    return f"{namespace}:{_stable_hash(value)}"


def _present(value: object) -> bool:
    return value is not None and value != ""


def _parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=value.tzinfo or UTC)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(value, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    if len(text) == 7 and text[4] == "-":
        text = f"{text}-01"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.replace(tzinfo=parsed.tzinfo or UTC)


def _observed_at(context: Mapping[str, object]) -> datetime:
    observed_at = _parse_datetime(context.get("observed_at"))
    if observed_at is None:
        raise ValueError("observed_at_required")
    return observed_at


def _safe_refs(namespace: str, values: object) -> tuple[str, ...]:
    if isinstance(values, Mapping):
        candidates: Sequence[object] = tuple(values.keys())
    elif isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
        candidates = values
    elif _present(values):
        candidates = (values,)
    else:
        candidates = ()
    return tuple(_opaque_ref(namespace, value) for value in candidates if _present(value))


def _provider_id(value: object) -> tuple[str | None, tuple[EvidenceError, ...]]:
    if _present(value):
        return _opaque_ref("provider-item", value), ()
    return None, (
        EvidenceError(
            code="provider_item_id_missing",
            field="provider_item_id",
            safe_message="Provider item ID is unavailable.",
        ),
    )


def _source_priority(context: Mapping[str, object]) -> int | None:
    value = context.get("source_priority")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _base(
    *,
    item: Mapping[str, object],
    context: Mapping[str, object],
    provider: Provider,
    item_type: ProviderItemType,
    source_type: SourceType,
    evidence_kind: EvidenceKind,
    provider_id_value: object,
    event_time: datetime | None,
    content_presence: ContentPresence | None = None,
    numeric_presence: NumericPresence | None = None,
    entity_refs: tuple[str, ...] = (),
    asset_refs: tuple[str, ...] = (),
    topic_refs: tuple[str, ...] = (),
    official: bool = False,
    market_data: bool = False,
    disclosure: bool = False,
    news: bool = False,
) -> CommonEvidenceEnvelope:
    provider_item_hash = _stable_hash(item)
    provider_item_id, errors = _provider_id(provider_id_value)
    return CommonEvidenceEnvelope(
        evidence_version=EVIDENCE_VERSION,
        provider=provider,
        provider_item_type=item_type,
        source_type=source_type,
        source_priority=_source_priority(context),
        access_level=AccessLevel.LINK_ONLY,
        provider_item_id=provider_item_id,
        provider_item_hash=provider_item_hash,
        canonical_source_reference=None,
        observed_at=_observed_at(context),
        event_time=event_time,
        entity_refs=entity_refs,
        asset_refs=asset_refs,
        topic_refs=topic_refs,
        dedup_candidate_key=f"candidate:{provider_item_hash}",
        evidence_kind=evidence_kind,
        evidence_confidence=None,
        content_presence=content_presence or ContentPresence(),
        numeric_presence=numeric_presence or NumericPresence(),
        official_source_flag=official,
        market_data_flag=market_data,
        disclosure_flag=disclosure,
        news_signal_flag=news,
        raw_payload_reference=f"internal://evidence/{provider.value}/{provider_item_hash}",
        processing_status=ProcessingStatus.BLOCKED if errors else ProcessingStatus.VALIDATED,
        errors=errors,
    )


def map_marketaux_news_to_evidence(
    item: Mapping[str, object], context: Mapping[str, object]
) -> CommonEvidenceEnvelope:
    return _base(
        item=item,
        context=context,
        provider=Provider.MARKETAUX,
        item_type=ProviderItemType.MARKETAUX_NEWS,
        source_type=SourceType.NEWS,
        evidence_kind=EvidenceKind.NEWS,
        provider_id_value=item.get("uuid"),
        event_time=_parse_datetime(item.get("published_at")),
        content_presence=ContentPresence(
            has_title=_present(item.get("title")),
            has_url=_present(item.get("url")),
            has_snippet=_present(item.get("snippet")),
            has_description=_present(item.get("description")),
        ),
        entity_refs=_safe_refs("entity", item.get("entities")),
        topic_refs=_safe_refs("topic", item.get("keywords")),
        news=True,
    )


def map_finnhub_quote_to_evidence(
    item: Mapping[str, object], context: Mapping[str, object]
) -> CommonEvidenceEnvelope:
    numeric_count = sum(
        isinstance(item.get(field), (int, float)) and not isinstance(item.get(field), bool)
        for field in _NUMERIC_QUOTE_FIELDS
    )
    symbol = context.get("symbol")
    return _base(
        item=item,
        context=context,
        provider=Provider.FINNHUB,
        item_type=ProviderItemType.FINNHUB_QUOTE,
        source_type=SourceType.MARKET_DATA,
        evidence_kind=EvidenceKind.MARKET_DATA,
        provider_id_value=(symbol, item.get("t")) if _present(symbol) else None,
        event_time=_parse_datetime(item.get("t")),
        numeric_presence=NumericPresence(
            has_numeric_value=numeric_count > 0, numeric_field_count=numeric_count
        ),
        asset_refs=_safe_refs("asset", symbol),
        market_data=True,
    )


def map_eia_energy_row_to_evidence(
    item: Mapping[str, object], context: Mapping[str, object]
) -> CommonEvidenceEnvelope:
    numeric_count = sum(
        isinstance(item.get(field), (int, float)) and not isinstance(item.get(field), bool)
        for field in ("price", "value")
    )
    return _base(
        item=item,
        context=context,
        provider=Provider.EIA,
        item_type=ProviderItemType.EIA_ENERGY_TIMESERIES,
        source_type=SourceType.OFFICIAL_ENERGY,
        evidence_kind=EvidenceKind.ENERGY_OFFICIAL,
        provider_id_value=(item.get("period"), item.get("sectorid"), item.get("stateid")),
        event_time=_parse_datetime(item.get("period")),
        numeric_presence=NumericPresence(
            has_numeric_value=numeric_count > 0,
            numeric_field_count=numeric_count,
            nullable_allowed=True,
        ),
        entity_refs=_safe_refs("entity", (item.get("stateid"), item.get("stateDescription"))),
        topic_refs=_safe_refs("topic", (item.get("sectorid"), item.get("sectorName"))),
        official=True,
    )


def map_sec_filing_to_evidence(
    item: Mapping[str, object], context: Mapping[str, object]
) -> CommonEvidenceEnvelope:
    event_time = next(
        (
            parsed
            for field in ("acceptanceDateTime", "filingDate", "reportDate")
            if (parsed := _parse_datetime(item.get(field))) is not None
        ),
        None,
    )
    return _base(
        item=item,
        context=context,
        provider=Provider.SEC_EDGAR,
        item_type=ProviderItemType.SEC_FILING,
        source_type=SourceType.DISCLOSURE,
        evidence_kind=EvidenceKind.DISCLOSURE,
        provider_id_value=item.get("accessionNumber"),
        event_time=event_time,
        content_presence=ContentPresence(has_url=_present(item.get("primaryDocument"))),
        asset_refs=_safe_refs("asset", context.get("ticker")),
        topic_refs=_safe_refs("topic", item.get("form")),
        official=True,
        disclosure=True,
    )
