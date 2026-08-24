# SPEC-0042 — Durable Safe Factual Projection Implementation

Status: Active — Implementation Review

Phase: Pre-AI Collection Readiness R2

## Authorization and boundary

This SPEC implements the approved R2 durable handoff from collection into restart-safe factual projections.
Production collection authority remains `legacy`; this change does not activate the unified control plane, run a
production migration, perform cutover, execute historical replay, or implement Migration B. PR #39 / SPEC-0040
remains an untouched Draft.

The atomic collection transaction is extended only to:

```text
CollectionRun
+ canonical RawItem
+ RawItemObservation
+ SafeFactProjection(PENDING)
```

It does not create ContentItem, EvidenceItem, EventCandidate, FactSnapshot, ImpactAnalysis, Notification, or any AI
runtime object. No Provider, Telegram, or AI request is part of implementation or verification.

## Persistence contract

Migration `0007` is additive and follows `0006`. `raw_item_observations` records each run's observation of one
canonical RawItem without changing `RawItem.collection_run_id`. The unique `(collection_run_id, raw_item_id)` pair
is the idempotency identity. Observation kind is deterministic:

- a canonical insert is `first_seen`;
- an existing canonical item with the same projection hash is `duplicate_same_projection`;
- an existing canonical item with a different projection hash is `revision_candidate`.

Database triggers enforce run/raw/target/source/account/provider/operation/config/contract provenance.
`safe_fact_projections` stores one schema version per observation, an exact canonical-JSON hash, quality and
processing state, bounded retry metadata, and a typed allowlisted factual object. Invalid data cannot become
`ready`. Downgrade is allowed only when both R2 tables are empty; it never discards R2 state silently.

## Projection schema v1

Only these exact provider-neutral boundaries are accepted:

- `marketaux/news_all`: item identity/time, permitted title and public canonical URL, source identity, reviewed
  query/language/symbol target context; description/snippet/body remain blocked and are not saved.
- `finnhub/quote`: symbol, provider timestamp and the actual `c/d/dp/h/l/o/pc` numeric values; unknown
  currency/exchange is explicit and never guessed.
- `eia/electricity_retail_sales`: period, dataset/series, geography, sector, metric, actual numeric value and unit;
  a missing unit is explicit `unknown`.
- `sec_edgar/submissions_recent`: CIK, ticker, accession, filing date/form, primary document name and only an
  allowlisted official `https://www.sec.gov/Archives/...` reference. Filing bodies are never downloaded.

Provider/operation/schema-version mismatch, arbitrary fields, non-finite numeric values, secret markers and unsafe
URLs fail closed. The legacy sanitized metadata/display sidecars remain compatibility-only; the R2 transaction uses
a separate typed factual projection channel and never promotes legacy presence flags, counts, or zero placeholders.

## Validation worker

`SafeFactProjectionWorker` claims bounded PENDING/RETRY rows with PostgreSQL `FOR UPDATE SKIP LOCKED`, transitions
through VALIDATING, deterministically validates and hashes payloads, and produces READY or value-free BLOCKED
results. Stale VALIDATING claims recover through bounded retry and eventually block. Repeated runs are idempotent.
The worker has no Provider, collection scheduler, Telegram, Content/Evidence/Event, or AI dependency.

## Review gates

- Migration `0006 -> 0007`, empty-state downgrade/upgrade, and a single Alembic head pass.
- Atomic persistence, rollback, classification, idempotency, concurrent duplicate observation and provenance pass.
- All four typed contracts preserve approved factual values and reject unsafe data.
- Worker claim, retry, stale recovery, unsafe blocking and repeat execution pass.
- Existing Phase 1/R1 scheduler, Provider and Telegram regressions pass without external requests.

## Explicit non-goals

No production activation/cutover, Phase 2 production migration, Migration B, historical replay, operation expansion,
new Provider, Content/Evidence/Event/Fact/AI runtime expansion, Market Validation, recommendation, real external
request, `.env` read, credential read, raw response persistence, or PR #39 change is authorized.

## Review history

- 2026-08-24 — implementation opened for independent review; no merge or runtime activation authorized.
