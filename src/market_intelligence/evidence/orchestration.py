"""Content-free RawItem-to-evidence orchestration boundary."""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from market_intelligence.evidence.contracts import (
    CommonEvidenceEnvelope,
    validate_evidence_envelope,
)
from market_intelligence.evidence.provider_mappings import (
    map_eia_energy_row_to_evidence,
    map_finnhub_quote_to_evidence,
    map_marketaux_news_to_evidence,
    map_sec_filing_to_evidence,
)
from market_intelligence.evidence.write_path import (
    EvidenceWriteOutcome,
    EvidenceWriteRequest,
    EvidenceWriteStatus,
)

_SUPPORTED = frozenset({"marketaux", "finnhub", "eia", "sec_edgar"})
_HASH = re.compile(r"^[0-9a-f]{64}$")
_SECRET = re.compile(
    r"(?i)(api[_-]?key|api[_-]?token|authorization|x-finnhub-token|token|secret|password)"
)
_SAFE_PROJECTION_FIELDS = {
    "marketaux": frozenset(
        {
            "provider_item_id",
            "published_at",
            "field_names",
            "has_title",
            "has_description",
            "has_snippet",
            "has_source_url",
            "payload_hash",
            "payload_reference",
        }
    ),
    "finnhub": frozenset(
        {
            "provider_item_id",
            "published_at",
            "field_names",
            "symbol",
            "numeric_field_count",
            "payload_hash",
            "payload_reference",
        }
    ),
    "eia": frozenset(
        {
            "provider_item_id",
            "published_at",
            "field_names",
            "geography",
            "sector",
            "has_numeric_value",
            "payload_hash",
            "payload_reference",
        }
    ),
    "sec_edgar": frozenset(
        {
            "provider_item_id",
            "published_at",
            "field_names",
            "ticker",
            "form",
            "has_primary_document",
            "payload_hash",
            "payload_reference",
        }
    ),
}


class EvidencePipelineStatus(StrEnum):
    WRITTEN = "written"
    DUPLICATE = "duplicate"
    INVALID = "invalid"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class EvidencePipelineError:
    code: str
    field: str | None
    safe_message: str


@dataclass(frozen=True, slots=True)
class EvidencePipelineRequest:
    raw_item_id: uuid.UUID
    source_id: uuid.UUID
    source_account_id: uuid.UUID | None
    provider: str
    sanitized_projection: Mapping[str, object]
    observed_at: datetime
    correlation_id: str


@dataclass(frozen=True, slots=True)
class EvidencePipelineOutcome:
    status: EvidencePipelineStatus
    raw_item_id: uuid.UUID
    evidence_item_id: uuid.UUID | None
    safe_errors: tuple[EvidencePipelineError, ...]
    provider: str
    provider_item_hash: str | None


class EvidenceWriter(Protocol):
    async def write_one(self, request: EvidenceWriteRequest) -> EvidenceWriteOutcome: ...


class EvidencePipelineService:
    """Map a sanitized projection and delegate all persistence to EvidenceWriteService."""

    def __init__(self, writer: EvidenceWriter) -> None:
        self._writer = writer

    async def process(self, request: EvidencePipelineRequest) -> EvidencePipelineOutcome:
        if request.provider not in _SUPPORTED:
            return _failed(request, EvidencePipelineStatus.SKIPPED, "provider_unsupported")

        projection = validate_provider_projection(request.provider, request.sanitized_projection)
        if projection is None:
            return _failed(request, EvidencePipelineStatus.INVALID, "projection_invalid")
        if request.observed_at.tzinfo is None or not _safe_text(request.correlation_id):
            return _failed(request, EvidencePipelineStatus.INVALID, "request_invalid")

        try:
            envelope = _map_projection(request.provider, projection, request.observed_at)
            envelope = replace(
                envelope,
                provider_item_hash=str(projection["payload_hash"]),
                raw_payload_reference=str(projection["payload_reference"]),
            )
        except (TypeError, ValueError):
            return _failed(request, EvidencePipelineStatus.INVALID, "mapping_failed")

        validation_errors = validate_evidence_envelope(envelope)
        if validation_errors:
            return _failed(request, EvidencePipelineStatus.INVALID, "envelope_invalid")

        write_outcome = await self._writer.write_one(
            EvidenceWriteRequest(
                envelope=envelope,
                source_id=request.source_id,
                source_account_id=request.source_account_id,
                raw_item_id=request.raw_item_id,
            )
        )
        return _write_outcome(request, write_outcome)


def validate_marketaux_projection(
    projection: Mapping[str, object],
) -> dict[str, object] | None:
    return validate_provider_projection("marketaux", projection)


def validate_provider_projection(
    provider: str, projection: Mapping[str, object]
) -> dict[str, object] | None:
    expected = _SAFE_PROJECTION_FIELDS.get(provider)
    if expected is None or set(projection) != expected:
        return None
    item_id = projection.get("provider_item_id")
    published_at = projection.get("published_at")
    field_names = projection.get("field_names")
    payload_hash = projection.get("payload_hash")
    payload_reference = projection.get("payload_reference")
    if not _safe_text(item_id) or not _safe_text(published_at):
        return None
    if not isinstance(field_names, (tuple, list)) or any(
        not _safe_text(field) for field in field_names
    ):
        return None
    if not isinstance(payload_hash, str) or _HASH.fullmatch(payload_hash) is None:
        return None
    if not isinstance(payload_reference, str) or not payload_reference.startswith(
        ("internal://", "capture://", "local-ref://")
    ):
        return None
    if _SECRET.search(payload_reference):
        return None
    if provider == "marketaux" and not all(
        isinstance(projection.get(flag), bool)
        for flag in ("has_title", "has_description", "has_snippet", "has_source_url")
    ):
        return None
    if provider == "finnhub" and (
        not _safe_text(projection.get("symbol"))
        or not isinstance(projection.get("numeric_field_count"), int)
    ):
        return None
    if provider == "eia" and (
        not _safe_text(projection.get("geography"))
        or not _safe_text(projection.get("sector"))
        or not isinstance(projection.get("has_numeric_value"), bool)
    ):
        return None
    if provider == "sec_edgar" and (
        not _safe_text(projection.get("ticker"))
        or not _safe_text(projection.get("form"))
        or not isinstance(projection.get("has_primary_document"), bool)
    ):
        return None
    return dict(projection)


def _map_projection(
    provider: str, projection: Mapping[str, object], observed_at: datetime
) -> CommonEvidenceEnvelope:
    context: dict[str, object] = {"observed_at": observed_at}
    item: dict[str, object] = {"published_at": projection["published_at"]}
    if provider == "marketaux":
        item.update(
            {
                "uuid": projection["provider_item_id"],
                "title": True if projection["has_title"] else None,
                "description": True if projection["has_description"] else None,
                "snippet": True if projection["has_snippet"] else None,
                "url": True if projection["has_source_url"] else None,
            }
        )
        return map_marketaux_news_to_evidence(item, context)
    if provider == "finnhub":
        item.update({"t": projection["published_at"]})
        numeric_field_count = projection["numeric_field_count"]
        assert isinstance(numeric_field_count, int)
        for index in range(numeric_field_count):
            item[("c", "d", "dp", "h", "l", "o", "pc")[index]] = 0
        context["symbol"] = projection["symbol"]
        return map_finnhub_quote_to_evidence(item, context)
    if provider == "eia":
        item.update(
            {
                "period": projection["published_at"],
                "stateid": projection["geography"],
                "sectorid": projection["sector"],
                "price": 0 if projection["has_numeric_value"] else None,
            }
        )
        return map_eia_energy_row_to_evidence(item, context)
    item.update(
        {
            "accessionNumber": projection["provider_item_id"],
            "filingDate": projection["published_at"],
            "form": projection["form"],
            "primaryDocument": True if projection["has_primary_document"] else None,
        }
    )
    context["ticker"] = projection["ticker"]
    return map_sec_filing_to_evidence(item, context)


def _safe_text(value: object) -> bool:
    return isinstance(value, str) and bool(value) and _SECRET.search(value) is None


def _failed(
    request: EvidencePipelineRequest,
    status: EvidencePipelineStatus,
    code: str,
) -> EvidencePipelineOutcome:
    provider = request.provider if request.provider in _SUPPORTED else "unknown"
    return EvidencePipelineOutcome(
        status=status,
        raw_item_id=request.raw_item_id,
        evidence_item_id=None,
        safe_errors=(EvidencePipelineError(code=code, field=None, safe_message=code),),
        provider=provider,
        provider_item_hash=None,
    )


def _write_outcome(
    request: EvidencePipelineRequest,
    outcome: EvidenceWriteOutcome,
) -> EvidencePipelineOutcome:
    status = {
        EvidenceWriteStatus.INSERTED: EvidencePipelineStatus.WRITTEN,
        EvidenceWriteStatus.EXISTING: EvidencePipelineStatus.DUPLICATE,
        EvidenceWriteStatus.DUPLICATE: EvidencePipelineStatus.DUPLICATE,
        EvidenceWriteStatus.BLOCKED: EvidencePipelineStatus.INVALID,
        EvidenceWriteStatus.INVALID: EvidencePipelineStatus.INVALID,
        EvidenceWriteStatus.FAILED: EvidencePipelineStatus.FAILED,
    }[outcome.status]
    return EvidencePipelineOutcome(
        status=status,
        raw_item_id=request.raw_item_id,
        evidence_item_id=outcome.evidence_item_id,
        safe_errors=tuple(
            EvidencePipelineError(error.code, error.field, error.safe_message)
            for error in outcome.errors
        ),
        provider=outcome.provider,
        provider_item_hash=outcome.provider_item_hash,
    )
