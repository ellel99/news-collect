"""Provider-neutral, provenance-preserving Event fact snapshots."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from market_intelligence.db.models import (
    ContentItem,
    EventCandidate,
    EventCandidateEvidence,
    EvidenceItem,
)


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
                .outerjoin(ContentItem, ContentItem.id == EvidenceItem.content_item_id)
                .where(
                    EventCandidateEvidence.event_candidate_id == event_candidate_id,
                    EventCandidateEvidence.active.is_(True),
                )
                .order_by(EvidenceItem.observed_at, EvidenceItem.id)
            )
        ).all()
        if not rows:
            raise ValueError("event_fact_evidence_insufficient")
        evidence = [row[0] for row in rows]
        contents = [row[1] for row in rows if row[1] is not None]
        titles = tuple(
            sorted({item.title.strip() for item in contents if item.title and item.title.strip()})
        )
        what_happened = (
            candidate.fact_summary or candidate.canonical_title or (titles[0] if titles else "")
        )
        if not what_happened:
            raise ValueError("event_fact_summary_unavailable")
        source_types = tuple(sorted({str(item.source_type) for item in evidence}))
        providers = tuple(sorted({item.provider for item in evidence}))
        official = any(item.official_source_flag for item in evidence)
        contradictions = ("multiple_content_safe_titles",) if len(titles) > 1 else ()
        uncertainty = tuple(
            item
            for item, condition in (
                ("single_source", len({value.source_id for value in evidence}) == 1),
                ("no_official_evidence", not official),
                ("contradictory_projections", bool(contradictions)),
            )
            if condition
        )
        values = {
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
            "source_count": len({item.source_id for item in evidence}),
            "official_evidence_present": official,
            "source_types": source_types,
            "evidence_refs": tuple(sorted(str(item.id) for item in evidence)),
            "corroboration": (
                ("cross_source",) if len({item.source_id for item in evidence}) > 1 else ()
            ),
            "contradictions": contradictions,
            "uncertainty": uncertainty,
            "freshness": "current",
            "provenance_summary": tuple(f"provider:{provider}" for provider in providers),
        }
        snapshot_hash = hashlib.sha256(
            json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return FactSnapshot(
            event_candidate_id=candidate.id,
            fact_version=1,
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
            source_count=len({item.source_id for item in evidence}),
            official_evidence_present=official,
            source_types=source_types,
            evidence_refs=tuple(sorted(str(item.id) for item in evidence)),
            corroboration=(
                ("cross_source",) if len({item.source_id for item in evidence}) > 1 else ()
            ),
            contradictions=contradictions,
            uncertainty=uncertainty,
            freshness="current",
            provenance_summary=tuple(f"provider:{provider}" for provider in providers),
            snapshot_hash=snapshot_hash,
        )


def safe_fact_payload(value: FactSnapshot) -> dict[str, object]:
    """Return the bounded AI input; no raw payload, secret, or unrestricted body exists here."""

    payload = asdict(value)
    payload.pop("snapshot_hash")
    payload["event_candidate_id"] = str(value.event_candidate_id)
    for field in ("occurred_at", "first_seen_at", "latest_seen_at"):
        item = getattr(value, field)
        payload[field] = _time(item)
    return payload


def _values(items: list[EvidenceItem], name: str) -> tuple[str, ...]:
    return tuple(sorted({str(value) for item in items for value in (getattr(item, name) or [])}))


def _time(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
