# SPEC-0043 — R8-A SafeFactProjection → Evidence Durable Handoff

Status: Active — Implementation Review

## Scope

Implement the bounded, restart-safe handoff from READY `SafeFactProjection` rows to canonical
`EvidenceItem` rows. `SafeFactProjection.factual_payload` remains the only durable rich factual payload;
`EvidenceItem` stores canonical identity and provenance only. This SPEC adds Alembic `0008`, the
`evidence_projection_links` state/lineage table, a bounded worker, and authority-neutral Celery
reconciliation.

## Invariants

- One link per projection; multiple revision projections may link to one canonical EvidenceItem.
- Canonical Evidence is first resolved by `raw_item_id + provider`; existing identity/provenance is never
  overwritten. Conflicts become value-free BLOCKED outcomes.
- The first canonical projection hash remains `EvidenceItem.provider_item_hash`; every revision hash remains
  durable on its linked SafeFactProjection.
- Link, newly required ContentItem, and newly required EvidenceItem finalize in one transaction.
- PostgreSQL guards enforce READY projection eligibility and null-safe raw/source/account/provider provenance.
- Finnhub/EIA values are read through the link and are never reconstructed from counts, flags, or zeros.
- Marketaux creates Content only with title, canonical URL, and source identity. SEC creates/adopts only
  official link-only Content. Finnhub/EIA create no Content or Notification.
- The worker uses bounded discovery, `FOR UPDATE SKIP LOCKED`, finite retry, stale recovery, and periodic
  reconciliation. Reports contain counts only.

## Compatibility

Legacy Content/Evidence may be adopted only when identity and provenance agree. The compatibility path is
not deleted, but this worker must not import or invoke `provider_mappings.py` or EvidenceWriteService. It does
not trigger EventCandidate, Event, Fact, ImpactAnalysis, Notification, Rich Evidence, or AI.

## Non-goals

No production activation/cutover, Phase 2 production migration, Migration B, historical replay, Provider
operation expansion, raw response persistence, external request, or modification of PR #39. Production
authority remains `legacy`.

## Review history

- 2026-08-24: implementation authorized from `main@6d5cdac`; review pending.
