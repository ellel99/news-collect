"""Provider-neutral, bounded and provenance-preserving Event fact snapshots."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from market_intelligence.db.models import (
    ContentItem,
    EventCandidate,
    EventCandidateEvidence,
    EventFactSnapshotRecord,
    EvidenceItem,
)

MAX_EVIDENCE_DIGESTS_PER_EVENT = 12
MAX_TITLE_CHARS = 500
MAX_SUMMARY_CHARS = 1000
MAX_STRUCTURED_FACT_FIELDS = 16


class AnalysisInputQuality(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


@dataclass(frozen=True, slots=True)
class EvidenceFactDigest:
    evidence_ref: str
    provider: str
    source_type: str
    official_source: bool
    event_time: datetime | None
    published_at: datetime | None
    title: str | None
    summary: str | None
    entity_refs: tuple[str, ...]
    asset_refs: tuple[str, ...]
    topic_refs: tuple[str, ...]
    structured_facts: tuple[tuple[str, str | int | float | bool | None], ...]
    provenance_ref: str


@dataclass(frozen=True, slots=True)
class FactSnapshot:
    event_candidate_id: UUID
    fact_version: int
    what_happened: str
    primary_entities: tuple[str, ...]
    companies: tuple[str, ...]
    assets: tuple[str, ...]
    sectors: tuple[str, ...]
    topics: tuple[str, ...]
    occurred_at: datetime | None
    first_seen_at: datetime
    latest_seen_at: datetime
    evidence_count: int
    source_count: int
    official_evidence_present: bool
    source_types: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    evidence_digests: tuple[EvidenceFactDigest, ...]
    evidence_digest_total_count: int
    evidence_digest_included_count: int
    evidence_digest_truncated: bool
    analysis_input_quality: AnalysisInputQuality
    corroboration: tuple[str, ...]
    contradictions: tuple[str, ...]
    uncertainty: tuple[str, ...]
    freshness: str
    provenance_summary: tuple[str, ...]
    snapshot_hash: str


class FactLayerBuilder:
    async def build(self, session: AsyncSession, event_candidate_id: UUID) -> FactSnapshot:
        candidate = await session.get(EventCandidate, event_candidate_id)
        if candidate is None:
            raise ValueError("event_candidate_not_found")
        rows = (
            await session.execute(
                select(EvidenceItem, ContentItem)
                .join(
                    EventCandidateEvidence,
                    EventCandidateEvidence.evidence_item_id == EvidenceItem.id,
                )
                .outerjoin(ContentItem, ContentItem.raw_item_id == EvidenceItem.raw_item_id)
                .where(
                    EventCandidateEvidence.event_candidate_id == event_candidate_id,
                    EventCandidateEvidence.active.is_(True),
                )
            )
        ).all()
        if not rows:
            raise ValueError("event_fact_evidence_insufficient")
        typed_rows = cast(list[tuple[EvidenceItem, ContentItem | None]], list(rows))
        ordered = sorted(typed_rows, key=_digest_order)
        evidence = [row[0] for row in ordered]
        all_digests = tuple(_digest(item, content) for item, content in ordered)
        digests = all_digests[:MAX_EVIDENCE_DIGESTS_PER_EVENT]
        titles = tuple(sorted({item.title for item in digests if item.title}))
        what_happened = (
            candidate.fact_summary or candidate.canonical_title or (titles[0] if titles else "")
        )
        if not what_happened:
            raise ValueError("event_fact_summary_unavailable")
        official = any(item.official_source_flag for item in evidence)
        source_count = len({item.source_id for item in evidence})
        contradictions = ("multiple_content_safe_titles",) if len(titles) > 1 else ()
        quality = _quality(digests, source_count, official)
        uncertainty = tuple(
            item
            for item, condition in (
                ("single_source", source_count == 1),
                ("no_official_evidence", not official),
                ("contradictory_projections", bool(contradictions)),
                ("insufficient_evidence_context", quality is AnalysisInputQuality.LOW),
                ("evidence_digest_truncated", len(all_digests) > len(digests)),
            )
            if condition
        )
        providers = tuple(sorted({item.provider for item in evidence}))
        values: dict[str, Any] = {
            "event_candidate_id": str(candidate.id),
            "what_happened": what_happened,
            "primary_entities": _values(evidence, "entity_refs"),
            "companies": tuple(str(value) for value in candidate.companies),
            "assets": _values(evidence, "asset_refs"),
            "sectors": tuple(str(value) for value in candidate.sectors),
            "topics": _values(evidence, "topic_refs"),
            "occurred_at": _time(candidate.occurred_at),
            "first_seen_at": _time(candidate.first_seen_at),
            "latest_seen_at": _time(candidate.latest_seen_at),
            "evidence_count": len(evidence),
            "source_count": source_count,
            "official_evidence_present": official,
            "source_types": tuple(sorted({str(item.source_type) for item in evidence})),
            "evidence_refs": tuple(sorted(str(item.id) for item in evidence)),
            "evidence_digests": tuple(evidence_digest_payload(item) for item in digests),
            "evidence_digest_total_count": len(all_digests),
            "evidence_digest_included_count": len(digests),
            "evidence_digest_truncated": len(all_digests) > len(digests),
            "analysis_input_quality": quality.value,
            "corroboration": ("cross_source",) if source_count > 1 else (),
            "contradictions": contradictions,
            "uncertainty": uncertainty,
            "freshness": "current",
            "provenance_summary": tuple(f"provider:{provider}" for provider in providers),
        }
        snapshot_hash = hashlib.sha256(
            json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        version = await _fact_version(
            session,
            candidate.id,
            snapshot_hash,
            len(all_digests),
            len(digests),
            quality,
        )
        return FactSnapshot(
            event_candidate_id=candidate.id,
            fact_version=version,
            what_happened=what_happened,
            primary_entities=_values(evidence, "entity_refs"),
            companies=tuple(str(value) for value in candidate.companies),
            assets=_values(evidence, "asset_refs"),
            sectors=tuple(str(value) for value in candidate.sectors),
            topics=_values(evidence, "topic_refs"),
            occurred_at=candidate.occurred_at,
            first_seen_at=candidate.first_seen_at,
            latest_seen_at=candidate.latest_seen_at,
            evidence_count=len(evidence),
            source_count=source_count,
            official_evidence_present=official,
            source_types=tuple(sorted({str(item.source_type) for item in evidence})),
            evidence_refs=tuple(sorted(str(item.id) for item in evidence)),
            evidence_digests=digests,
            evidence_digest_total_count=len(all_digests),
            evidence_digest_included_count=len(digests),
            evidence_digest_truncated=len(all_digests) > len(digests),
            analysis_input_quality=quality,
            corroboration=("cross_source",) if source_count > 1 else (),
            contradictions=contradictions,
            uncertainty=uncertainty,
            freshness="current",
            provenance_summary=tuple(f"provider:{provider}" for provider in providers),
            snapshot_hash=snapshot_hash,
        )


async def _fact_version(
    session: AsyncSession,
    candidate_id: UUID,
    snapshot_hash: str,
    total: int,
    included: int,
    quality: AnalysisInputQuality,
) -> int:
    existing = await session.scalar(
        select(EventFactSnapshotRecord).where(
            EventFactSnapshotRecord.event_candidate_id == candidate_id,
            EventFactSnapshotRecord.snapshot_hash == snapshot_hash,
        )
    )
    if existing is not None:
        return int(existing.fact_version)
    current_version = int(
        await session.scalar(
            select(func.coalesce(func.max(EventFactSnapshotRecord.fact_version), 0)).where(
                EventFactSnapshotRecord.event_candidate_id == candidate_id
            )
        )
        or 0
    )
    next_version: int = current_version + 1
    row = EventFactSnapshotRecord(
        event_candidate_id=candidate_id,
        fact_version=next_version,
        snapshot_hash=snapshot_hash,
        evidence_total_count=total,
        evidence_included_count=included,
        evidence_truncated=total > included,
        input_quality=quality.value,
    )
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError:
        existing = await session.scalar(
            select(EventFactSnapshotRecord).where(
                EventFactSnapshotRecord.event_candidate_id == candidate_id,
                EventFactSnapshotRecord.snapshot_hash == snapshot_hash,
            )
        )
        if existing is None:
            raise ValueError("event_fact_version_conflict") from None
        return int(existing.fact_version)
    return next_version


def safe_fact_payload(value: FactSnapshot) -> dict[str, object]:
    payload = asdict(value)
    payload.pop("snapshot_hash")
    payload["event_candidate_id"] = str(value.event_candidate_id)
    payload["analysis_input_quality"] = value.analysis_input_quality.value
    for field in ("occurred_at", "first_seen_at", "latest_seen_at"):
        payload[field] = _time(getattr(value, field))
    return payload


def _digest(item: EvidenceItem, content: ContentItem | None) -> EvidenceFactDigest:
    metadata = (
        content.metadata_ if content is not None and isinstance(content.metadata_, dict) else {}
    )
    raw_facts = metadata.get("structured_facts", {})
    structured = (
        tuple(
            (str(key), value)
            for key, value in sorted(raw_facts.items())[:MAX_STRUCTURED_FACT_FIELDS]
            if isinstance(key, str)
            and (value is None or isinstance(value, str | int | float | bool))
        )
        if isinstance(raw_facts, dict)
        else ()
    )
    return EvidenceFactDigest(
        evidence_ref=str(item.id),
        provider=item.provider,
        source_type=str(item.source_type),
        official_source=item.official_source_flag,
        event_time=item.event_time,
        published_at=content.source_published_at if content is not None else item.event_time,
        title=_bounded(content.title if content is not None else None, MAX_TITLE_CHARS),
        summary=_bounded(
            content.source_summary if content is not None else None, MAX_SUMMARY_CHARS
        ),
        entity_refs=tuple(str(value) for value in item.entity_refs or []),
        asset_refs=tuple(str(value) for value in item.asset_refs or []),
        topic_refs=tuple(str(value) for value in item.topic_refs or []),
        structured_facts=structured,
        provenance_ref=f"evidence://{item.id}",
    )


def _digest_order(row: tuple[EvidenceItem, ContentItem | None]) -> tuple[object, ...]:
    item = row[0]
    return (
        not item.official_source_flag,
        -item.observed_at.timestamp(),
        item.provider,
        str(item.id),
    )


def _quality(
    digests: tuple[EvidenceFactDigest, ...], source_count: int, official: bool
) -> AnalysisInputQuality:
    rich = sum(bool(item.summary or item.structured_facts) for item in digests)
    if official and source_count > 1 and rich > 0:
        return AnalysisInputQuality.HIGH
    if rich > 0 or official or source_count > 1:
        return AnalysisInputQuality.MEDIUM
    return AnalysisInputQuality.LOW


def evidence_digest_payload(value: EvidenceFactDigest) -> dict[str, object]:
    payload = asdict(value)
    payload["event_time"] = _time(value.event_time)
    payload["published_at"] = _time(value.published_at)
    return payload


def _bounded(value: str | None, limit: int) -> str | None:
    if not value:
        return None
    normalized = " ".join(value.split())
    return normalized[:limit] or None


def _values(items: list[EvidenceItem], name: str) -> tuple[str, ...]:
    return tuple(sorted({str(value) for item in items for value in (getattr(item, name) or [])}))


def _time(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
