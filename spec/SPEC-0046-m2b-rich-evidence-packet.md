# SPEC-0046 — M2-B Rich Evidence Packet

Status: Active — Implementation Review

## Authorization and boundary

M2-A is merged and complete. This SPEC implements a provider-neutral, deterministic, typed and bounded
read model over `EvidenceProjectionLink(LINKED) → SafeFactProjection(READY) → Observation → RawItem →
EvidenceItem → optional allowlisted ContentItem` plus source/target/run provenance. The packet is not persisted:
the immutable factual lineage is the reproducible authority, avoiding a second drifting factual payload.
Production authority remains `legacy`. No Provider, Telegram or AI request, activation, cutover, replay,
EventCandidate association, Fact, ImpactAnalysis, Notification or AI record is authorized.

## Packet v1 contract

`RichEvidencePacket` is an immutable typed contract containing packet version/digest, canonical Evidence and
RawItem identity, provider/operation/evidence types, access and retention policy, event/observed time, source,
account, first-persistence run, current target/config/contract provenance, optional content availability,
typed current facts, ordered revision lineage, quality, explicit missing/blocked fields and truncation metadata.
The digest is SHA-256 over canonical sorted JSON excluding the digest itself. It excludes read time, insertion
order and database natural order.

Six v1 variants are allowlisted: Marketaux `news_all`, Finnhub `quote` and `company_news`, EIA
`electricity_retail_sales` and `electricity_rto_region_data`, and SEC `submissions_recent`. Unknown provider,
operation or projection schema fails closed. Every read re-runs the R2 typed normalizer/classifier, compares the
canonical payload and projection hash, and validates Evidence/Projection/Observation/RawItem/Run/Target/Source
provenance. RawItem payload storage is never read.

## Revision and budget semantics

One canonical Evidence may have many linked projections. Revisions sort by `(observed_at, projection_id)`;
the greatest key is current. Equal timestamps therefore have a stable UUID tie-breaker. Revision history is
bounded (default 50, hard maximum 500), batch reads are stable Evidence UUID keysets (default 50, hard maximum
500), and canonical serialized bytes are bounded (default 256 KiB, hard maximum 2 MiB). Truncation retains the
current revision, reports total/included counts and an explicit reason, and never upgrades partial data to
complete. Revisions are not cross-source contradictions; M2-C owns association and contradiction semantics.

## Missing, coverage and access semantics

`None` remains missing/unknown; blocked coverage remains blocked. Absence is never reconstructed as zero, false
or empty text. Marketaux exposes saved title, safe URL, source/time/query/language/symbol context only; description
and snippet remain blocked. Finnhub company news exposes saved title/safe URL/source/category/symbol/time while
summary remains blocked. Finnhub quote preserves `c/d/dp/h/l/o/pc`, timestamp, symbol, currency and exchange.
EIA preserves series/period/facets/value/unit, including explicit unknown unit and partial quality. SEC exposes
filing identity/date/form/document and validated Archives URL only; body remains unavailable/link-only.
Access policy is inherited from canonical Evidence/RawItem and never broadened.

## Database protection

Migration 0010 (parent 0009) adds a PostgreSQL trigger. Once a READY projection is referenced by a LINKED handoff,
DELETE and changes to `raw_item_id`, `observation_id`, `provider`, `operation_key`, schema version,
`factual_payload`, `projection_hash`, or `quality_status` fail closed. The explicit bookkeeping allowlist remains:
`processing_status`, `safe_error_code`, `attempt_count`, `next_retry_at`, `processed_at`, and `updated_at`.
Existing linked Evidence/Content/link immutability remains in force. Downgrade refuses while linked state exists;
it never deletes factual data.

## Acceptance

- Six typed operation packets, complete/partial quality, optional/no Content and exact numeric facts.
- Deterministic digest/order, multi-revision current selection and explicit revision/byte truncation.
- PostgreSQL direct-SQL immutability, tamper/provenance/hash/unknown-contract fail-closed behavior.
- Repeated/concurrent reads create no writes and never invoke the legacy placeholder mapper.
- R1/R2/R8-A/M2-A, scheduler and Telegram regressions remain green; Alembic stays single-head 0010.
