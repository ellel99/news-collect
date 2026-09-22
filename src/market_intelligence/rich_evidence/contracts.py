"""Typed, immutable Rich Evidence Packet v1 contracts."""

from __future__ import annotations

import dataclasses
import uuid
from datetime import datetime
from typing import Literal

Quality = Literal["complete", "partial"]


@dataclasses.dataclass(frozen=True, slots=True)
class MarketauxNewsFacts:
    provider_item_id: str
    published_at: str
    title: str | None
    canonical_url: str | None
    source_identity: str | None
    query: str
    language: str | None
    symbols: tuple[str, ...] | None
    description_coverage: Literal["blocked"]
    snippet_coverage: Literal["blocked"]


@dataclasses.dataclass(frozen=True, slots=True)
class FinnhubQuoteFacts:
    provider_item_id: str
    published_at: str
    symbol: str
    provider_timestamp: int
    c: int | float
    d: int | float
    dp: int | float
    h: int | float
    l: int | float  # noqa: E741 - provider field name is part of the typed contract
    o: int | float
    pc: int | float
    currency: str
    exchange: str


@dataclasses.dataclass(frozen=True, slots=True)
class FinnhubCompanyNewsFacts:
    provider_item_id: str
    published_at: str
    title: str | None
    canonical_url: str | None
    source_identity: str | None
    symbol: str
    category: str | None
    summary_coverage: Literal["blocked"]


@dataclasses.dataclass(frozen=True, slots=True)
class EiaRetailFacts:
    provider_item_id: str
    published_at: str
    period: str
    dataset: str
    series_identity: str
    geography: str
    sector: str
    metric: str
    value: int | float
    unit: str


@dataclasses.dataclass(frozen=True, slots=True)
class EiaRtoFacts:
    provider_item_id: str
    published_at: str
    period: str
    dataset: str
    series_identity: str
    region: str
    metric: str
    value: int | float
    unit: str


@dataclasses.dataclass(frozen=True, slots=True)
class SecFilingFacts:
    provider_item_id: str
    published_at: str
    cik: str
    ticker: str
    accession_number: str
    filing_date: str
    form: str
    primary_document: str
    official_url: str
    official_source: Literal[True]
    submissions_file: str | None = None


type TypedFacts = (
    MarketauxNewsFacts
    | FinnhubQuoteFacts
    | FinnhubCompanyNewsFacts
    | EiaRetailFacts
    | EiaRtoFacts
    | SecFilingFacts
)


@dataclasses.dataclass(frozen=True, slots=True)
class SourceProvenance:
    source_id: uuid.UUID
    source_account_id: uuid.UUID | None
    collection_target_id: uuid.UUID | None
    first_persistence_run_id: uuid.UUID
    config_revision: int | None
    provider_contract_version: int


@dataclasses.dataclass(frozen=True, slots=True)
class ContentReference:
    content_item_id: uuid.UUID | None
    kind: str | None
    title_available: bool
    body_availability: str
    url_available: bool


@dataclasses.dataclass(frozen=True, slots=True)
class EvidenceRevision:
    link_id: uuid.UUID
    projection_id: uuid.UUID
    observation_id: uuid.UUID
    projection_hash: str
    projection_schema_version: int
    operation_key: str
    provider_contract_version: int
    collection_target_id: uuid.UUID | None
    config_revision: int | None
    linked_at: datetime
    observed_at: datetime
    quality: Quality
    facts: TypedFacts


@dataclasses.dataclass(frozen=True, slots=True)
class PacketTruncation:
    truncated: bool
    total_revision_count: int
    included_revision_count: int
    reason: Literal["none", "revision_budget", "serialized_size_budget"]


@dataclasses.dataclass(frozen=True, slots=True)
class RichEvidencePacket:
    packet_version: int
    packet_digest: str
    evidence_id: uuid.UUID
    raw_item_id: uuid.UUID
    provider: str
    operation_key: str
    evidence_kind: str
    provider_item_type: str
    access_level: str
    retention_class: str
    canonical_event_time: datetime | None
    canonical_observed_at: datetime
    current_published_at: datetime
    current_observed_at: datetime
    provenance: SourceProvenance
    content: ContentReference
    current: EvidenceRevision
    revisions: tuple[EvidenceRevision, ...]
    quality: Quality
    missing_fields: tuple[str, ...]
    blocked_fields: tuple[str, ...]
    truncation: PacketTruncation


@dataclasses.dataclass(frozen=True, slots=True)
class PacketPage:
    packets: tuple[RichEvidencePacket, ...]
    next_evidence_id: uuid.UUID | None
    scanned_count: int
    returned_count: int
    scan_exhausted: bool
    has_more: bool


class RichEvidenceError(ValueError):
    """Value-free, fail-closed packet construction error."""
