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
Canonical Evidence event/observed times are separate from the current revision published/observed times. All
times are timezone-aware and canonicalized to UTC. The digest is SHA-256 over canonical sorted UTF-8 JSON
excluding the digest itself. The serialized-size budget includes the final digest field and all JSON syntax.

Six v1 variants are allowlisted: Marketaux `news_all`, Finnhub `quote` and `company_news`, EIA
`electricity_retail_sales` and `electricity_rto_region_data`, and SEC `submissions_recent`. Unknown provider,
operation or projection schema fails closed. Every read re-runs the R2 typed normalizer/classifier, compares the
canonical payload and projection hash, and validates Evidence/Projection/Observation/RawItem/Run/Target/Source
provenance. RawItem payload storage is never read.

## Revision and budget semantics

One canonical Evidence may have many linked projections. Revisions sort by `(observed_at, projection_id)`;
the greatest key is current. Equal timestamps therefore have a stable UUID tie-breaker. Revision history is
bounded (default 50, hard maximum 500). Batch reads use stable Evidence UUID keysets with separate bounded
scan and result budgets: `limit` is returned packets, `scan_limit` is inspected Evidence, and the cursor is the
last scanned UUID. `scanned_count`, `returned_count`, `scan_exhausted`, and `has_more` make sparse post-filter
pages explicit without starvation or unbounded scans. Canonical serialized bytes are bounded (default 256 KiB,
hard maximum 2 MiB). Truncation retains the
current revision, reports total/included counts and an explicit reason, and never upgrades partial data to
complete. Revisions are not cross-source contradictions; M2-C owns association and contradiction semantics.

## Missing, coverage and access semantics

`None` remains missing/unknown; blocked coverage remains blocked. Absence is never reconstructed as zero, false
or empty text. Marketaux exposes saved title, safe URL, source/time/query/language/symbol context only; description
and snippet remain blocked. Finnhub company news exposes saved title/safe URL/source/category/symbol/time while
summary remains blocked. Finnhub quote preserves `c/d/dp/h/l/o/pc`, timestamp, symbol, currency and exchange.
EIA preserves series/period/facets/value/unit, including explicit unknown unit and partial quality. SEC exposes
filing identity/date/form/document and validated Archives URL only; body remains unavailable/link-only.
Access policy is inherited from canonical Evidence and operation policy and never broadened. RawItem and Source
retention classes must agree and be in the provider-operation allowlist. A disabled historical Source remains
readable only when its full lineage and retention policy still validate. Content is an optional safe reference:
Marketaux/Finnhub news must match a linked revision's title/URL and have unavailable body; quote/EIA forbid
Content; SEC requires official-release, unavailable body and a linked validated Archives URL.

## Database protection

Migration 0010 (parent 0009) adds a PostgreSQL trigger. Once a READY projection is referenced by a LINKED handoff,
DELETE fails closed. A whole-row comparison removes only `updated_at` before comparison, so every current and
future column defaults immutable. Linked projection status remains READY, error/retry remain null, and
`processed_at` remains non-null and immutable. `updated_at` is the sole bookkeeping allowlist field.
Existing linked Evidence/Content/link immutability remains in force. Downgrade refuses while linked state exists;
it never deletes factual data.

## Acceptance

- Six typed operation packets, complete/partial quality, optional/no Content and exact numeric facts.
- Deterministic digest/order, multi-revision current selection and explicit revision/byte truncation.
- PostgreSQL direct-SQL immutability, tamper/provenance/hash/unknown-contract fail-closed behavior.
- Repeated/concurrent reads create no writes and never invoke the legacy placeholder mapper.
- R1/R2/R8-A/M2-A, scheduler and Telegram regressions remain green; Alembic stays single-head 0010.
