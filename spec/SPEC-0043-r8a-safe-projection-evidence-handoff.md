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
- A READY row is not trusted by status alone. Before downstream writes, the worker reuses the R2 typed
  provider/operation/schema decoder, requires its normalized payload to be semantically identical to the stored
  payload, and requires the recomputed canonical hash to equal `projection_hash`. Failure is value-free BLOCKED;
  R8-A never repairs or overwrites the projection.
- Canonical Evidence is first resolved by `raw_item_id + provider`; existing identity/provenance is never
  overwritten. Adoption accepts only the R2 normalized identity or the exact deterministic legacy opaque identity
  computed by the compatibility identity algorithm. Conflicts become value-free BLOCKED outcomes.
- The first canonical projection hash remains `EvidenceItem.provider_item_hash`; every revision hash remains
  durable on its linked SafeFactProjection.
- Link, newly required ContentItem, and newly required EvidenceItem finalize in one transaction.
- PostgreSQL guards enforce READY eligibility, null-safe raw/source/account/provider provenance, provider item
  type, provider-specific Content policy, all-or-none linked state, and immutable linked associations.
- Finnhub/EIA values are read through the link and are never reconstructed from counts, flags, or zeros.
- Marketaux creates Content only with title, canonical URL, and source identity. SEC creates/adopts only
  official link-only Content. Finnhub/EIA create no Content or Notification.
- Content is the first canonical display projection. Later Marketaux title/URL or SEC document/URL revisions stay
  on SafeFactProjection and link to the same canonical Evidence and first Content; neither row is overwritten.
- Evidence access policy is explicit and provider-scoped: Marketaux `link_only`, Finnhub `licensed`, EIA
  `public_summary`, and SEC EDGAR `link_only`.
- The worker uses bounded discovery, `FOR UPDATE SKIP LOCKED`, finite retry, stale recovery, and periodic
  reconciliation. Each claimed item has an isolated transaction and safe exception boundary. Reports contain
  counts only.

## Compatibility

Legacy Content/Evidence may be adopted only when identity and provenance agree. The compatibility path is not
deleted. The worker may use the pure legacy identity calculator for adoption checks, but it must not invoke a
legacy placeholder mapper or EvidenceWriteService to generate new R8-A Evidence. Factual values always come from
SafeFactProjection. It does not trigger EventCandidate, Event, Fact, ImpactAnalysis, Notification, Rich Evidence,
or AI.

## Non-goals

No production activation/cutover, Phase 2 production migration, Migration B, historical replay, Provider
operation expansion, raw response persistence, external request, or modification of PR #39. Production
authority remains `legacy`.

## Review history

- 2026-08-24: implementation authorized from `main@6d5cdac`; review pending.
