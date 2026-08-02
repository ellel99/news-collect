"""Content-free RawItem-to-evidence orchestration boundary."""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from market_intelligence.evidence.contracts import validate_evidence_envelope
from market_intelligence.evidence.provider_mappings import map_marketaux_news_to_evidence
from market_intelligence.evidence.write_path import (
    EvidenceWriteOutcome,
    EvidenceWriteRequest,
    EvidenceWriteStatus,
)

_MARKETAUX = "marketaux"
_HASH = re.compile(r"^[0-9a-f]{64}$")
_SECRET = re.compile(
    r"(?i)(api[_-]?key|api[_-]?token|authorization|x-finnhub-token|token|secret|password)"
)
_SAFE_PROJECTION_FIELDS = frozenset(
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
)


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
        if request.provider != _MARKETAUX:
            return _failed(request, EvidencePipelineStatus.SKIPPED, "provider_unsupported")

        projection = _validated_marketaux_projection(request.sanitized_projection)
        if projection is None:
            return _failed(request, EvidencePipelineStatus.INVALID, "projection_invalid")
        if request.observed_at.tzinfo is None or not _safe_text(request.correlation_id):
            return _failed(request, EvidencePipelineStatus.INVALID, "request_invalid")

        mapper_item: dict[str, object] = {
            "uuid": projection["provider_item_id"],
            "published_at": projection["published_at"],
            "title": True if projection["has_title"] else None,
            "description": True if projection["has_description"] else None,
            "snippet": True if projection["has_snippet"] else None,
            "url": True if projection["has_source_url"] else None,
        }
        try:
            envelope = replace(
                map_marketaux_news_to_evidence(
                    mapper_item,
                    {"observed_at": request.observed_at},
                ),
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


def _validated_marketaux_projection(
    projection: Mapping[str, object],
) -> dict[str, object] | None:
    if set(projection) != _SAFE_PROJECTION_FIELDS:
        return None
    item_id = projection.get("provider_item_id")
    published_at = projection.get("published_at")
    field_names = projection.get("field_names")
    payload_hash = projection.get("payload_hash")
    payload_reference = projection.get("payload_reference")
    flags = (
        projection.get("has_title"),
        projection.get("has_description"),
        projection.get("has_snippet"),
        projection.get("has_source_url"),
    )
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
    if _SECRET.search(payload_reference) or not all(isinstance(flag, bool) for flag in flags):
        return None
    return dict(projection)


def _safe_text(value: object) -> bool:
    return isinstance(value, str) and bool(value) and _SECRET.search(value) is None


def _failed(
    request: EvidencePipelineRequest,
    status: EvidencePipelineStatus,
    code: str,
) -> EvidencePipelineOutcome:
    provider = request.provider if request.provider == _MARKETAUX else "unknown"
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
