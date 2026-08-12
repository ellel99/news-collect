"""Deterministic and explainable Level-1 EventCandidate matching."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

RULE_VERSION = 1
_TOKEN = re.compile(r"[a-z0-9]+")


class MatchRule(StrEnum):
    EXISTING_ASSOCIATION = "existing_association"
    OFFICIAL_IDENTITY = "official_identity"
    CANONICAL_URL = "canonical_url"
    PROVIDER_EXTERNAL_ID = "provider_external_id"
    ENTITY_TITLE_TIME = "entity_title_time"
    APPROVED_HASH = "approved_hash"
    NEW_CANDIDATE = "new_candidate"
    AMBIGUOUS_NEW_CANDIDATE = "ambiguous_new_candidate"


@dataclass(frozen=True, slots=True)
class EvidenceProjection:
    evidence_item_id: UUID
    provider: str
    provider_item_id: str | None
    provider_item_hash: str
    official_source: bool
    canonical_url: str | None
    title: str | None
    event_time: datetime | None
    observed_at: datetime
    entity_refs: tuple[str, ...] = ()
    asset_refs: tuple[str, ...] = ()
    topic_refs: tuple[str, ...] = ()
    source_priority: int | None = None


@dataclass(frozen=True, slots=True)
class CandidateProjection:
    id: UUID
    cluster_key: str
    strong_identity_hash: str | None
    identity_signatures: tuple[str, ...]
    title_fingerprints: tuple[str, ...]
    first_seen_at: datetime
    latest_seen_at: datetime
    entities: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StableAnchor:
    kind: str
    value_hash: str
    cluster_key: str


@dataclass(frozen=True, slots=True)
class MatchDecision:
    candidate_id: UUID | None
    match_rule: MatchRule
    rule_version: int
    anchor: StableAnchor
    signatures: tuple[str, ...]
    title_fingerprint: str | None
    strong_identity_hash: str | None


def stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def canonicalize_url(value: str | None) -> str | None:
    if value is None:
        return None
    parts = urlsplit(value.strip())
    if parts.scheme.casefold() not in {"http", "https"} or not parts.netloc:
        return None
    return urlunsplit(
        (parts.scheme.casefold(), parts.netloc.casefold(), parts.path.rstrip("/"), "", "")
    )


def title_fingerprint(value: str | None) -> str | None:
    tokens = _TOKEN.findall((value or "").casefold())
    return stable_hash(" ".join(tokens)) if tokens else None


def _signature(kind: str, value: str) -> str:
    return f"{kind}:{stable_hash(value)}"


def evidence_signatures(value: EvidenceProjection) -> tuple[str, ...]:
    signatures: list[str] = []
    if value.official_source and value.provider_item_id:
        signatures.append(_signature("official", f"{value.provider}:{value.provider_item_id}"))
    if canonical := canonicalize_url(value.canonical_url):
        signatures.append(_signature("url", canonical))
    if value.provider_item_id:
        signatures.append(_signature("provider", f"{value.provider}:{value.provider_item_id}"))
    signatures.append(_signature("hash", f"{value.provider}:{value.provider_item_hash}"))
    return tuple(signatures)


def derive_anchor(value: EvidenceProjection, *, ambiguous: bool = False) -> StableAnchor:
    signatures = evidence_signatures(value)
    if ambiguous:
        kind = "ambiguous_evidence"
        value_hash = stable_hash(str(value.evidence_item_id))
    else:
        priority = ("official", "url", "provider", "hash")
        selected = next(
            item for prefix in priority for item in signatures if item.startswith(f"{prefix}:")
        )
        kind, value_hash = selected.split(":", 1)
        if kind == "hash" and value.entity_refs and (fingerprint := title_fingerprint(value.title)):
            event_time = value.event_time or value.observed_at
            bucket = int(event_time.timestamp() // timedelta(hours=24).total_seconds())
            kind = "entity_title_time"
            value_hash = stable_hash(f"{sorted(value.entity_refs)}:{fingerprint}:{bucket}")
    return StableAnchor(
        kind=kind,
        value_hash=value_hash,
        cluster_key=stable_hash(f"{kind}:{value_hash}"),
    )


def match_existing(
    incoming: EvidenceProjection,
    candidates: tuple[CandidateProjection, ...],
    *,
    window: timedelta = timedelta(hours=24),
) -> MatchDecision:
    signatures = evidence_signatures(incoming)
    fingerprint = title_fingerprint(incoming.title)
    strong = next(
        (item.split(":", 1)[1] for item in signatures if item.startswith("official:")), None
    )
    ranked: list[tuple[int, MatchRule, CandidateProjection]] = []
    for candidate in candidates:
        shared = set(signatures) & set(candidate.identity_signatures)
        if any(item.startswith("official:") for item in shared):
            ranked.append((1, MatchRule.OFFICIAL_IDENTITY, candidate))
        elif any(item.startswith("url:") for item in shared):
            ranked.append((2, MatchRule.CANONICAL_URL, candidate))
        elif any(item.startswith("provider:") for item in shared):
            ranked.append((3, MatchRule.PROVIDER_EXTERNAL_ID, candidate))
        elif (
            fingerprint
            and fingerprint in candidate.title_fingerprints
            and set(incoming.entity_refs) & set(candidate.entities)
            and (incoming.event_time or incoming.observed_at) >= candidate.first_seen_at - window
            and (incoming.event_time or incoming.observed_at) <= candidate.latest_seen_at + window
        ):
            ranked.append((4, MatchRule.ENTITY_TITLE_TIME, candidate))
        elif any(item.startswith("hash:") for item in shared):
            ranked.append((5, MatchRule.APPROVED_HASH, candidate))
    if ranked:
        best_rank = min(item[0] for item in ranked)
        best = [item for item in ranked if item[0] == best_rank]
        if len(best) == 1:
            return MatchDecision(
                candidate_id=best[0][2].id,
                match_rule=best[0][1],
                rule_version=RULE_VERSION,
                anchor=derive_anchor(incoming),
                signatures=signatures,
                title_fingerprint=fingerprint,
                strong_identity_hash=strong,
            )
        return MatchDecision(
            candidate_id=None,
            match_rule=MatchRule.AMBIGUOUS_NEW_CANDIDATE,
            rule_version=RULE_VERSION,
            anchor=derive_anchor(incoming, ambiguous=True),
            signatures=signatures,
            title_fingerprint=fingerprint,
            strong_identity_hash=strong,
        )
    return MatchDecision(
        candidate_id=None,
        match_rule=MatchRule.NEW_CANDIDATE,
        rule_version=RULE_VERSION,
        anchor=derive_anchor(incoming),
        signatures=signatures,
        title_fingerprint=fingerprint,
        strong_identity_hash=strong,
    )
