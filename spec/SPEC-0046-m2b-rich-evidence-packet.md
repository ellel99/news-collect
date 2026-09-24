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

One canonical Evidence may have many linked projections. Migration 0010 persists separate unique
`canonical_evidence` and `canonical_content` markers on the exact handoff association; neither identity is
inferred from timestamps, insertion order or random UUIDs, and neither may change. Evidence may be adopted from
an earlier partial revision while the first later safe Content revision becomes canonical Content. Revisions
sort by `(observed_at, projection_id)`;
the greatest key is current. Equal timestamps therefore have a stable UUID tie-breaker. Revision history is
bounded (default 50, hard maximum 500). Batch reads use stable Evidence UUID keysets with separate bounded
scan and result budgets: `limit` is returned packets, `scan_limit` (hard maximum 500) is inspected Evidence, and
the cursor is the last scanned UUID. `scanned_count`, `returned_count`, `scan_exhausted`, and `has_more` make
sparse post-filter pages explicit without starvation or unbounded scans. A malformed packet fails the page
closed rather than being silently skipped. Canonical serialized bytes are bounded (default 256 KiB,
hard maximum 2 MiB). Truncation retains the
current revision, reports total/included counts and an explicit reason, and never upgrades partial data to
complete. Revisions are not cross-source contradictions; M2-C owns association and contradiction semantics.

The builder prefetches each bounded 100-Evidence chunk with set-based lineage loads. Including the transaction
snapshot statement, the query-count gate is 8 statements for scans of 1 or 50 and 36 for the maximum scan of
500; query count grows by bounded chunks rather than approximately one packet at a time.

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

Migration 0010 (parent 0009) adds stopped-writer PostgreSQL guards for the full durable lineage. A LINKED
association cannot be deleted and is whole-row immutable except `updated_at`; its Projection, Observation,
canonical RawItem and Evidence cannot be deleted or rewritten. Linked Content is whole-row immutable except
`updated_at`, and Source retention cannot change while linked lineage exists. Linked projection status remains
READY, error/retry remain null, and `processed_at` remains non-null and immutable. Future columns default
immutable. Non-linked worker recovery/cleanup remains legal. Downgrade fails closed while any LINKED state exists.

Each packet is built inside one PostgreSQL repeatable-read, read-only transaction. Historical target revision and
contract values come from frozen Observation/Run lineage; current mutable target versions, pause or retirement do
not invalidate historical packets. Target is consulted only for stable target/source/account/operation identity.
Existing linked Evidence/Content/link immutability remains in force. Downgrade refuses while linked state exists;
it never deletes factual data.

Handoff uses a fixed PostgreSQL row-lock order and bounded local lock timeout. Mutation triggers do not acquire
advisory locks after PostgreSQL has already taken the mutated row lock. Deadlock, serialization, lock-unavailable
and timeout outcomes become classified, value-free per-item retries; one conflicted item cannot end the batch.
Handoff locks Projection, Observation, Run, Target, SourceAccount, downstream canonical rows and association. After the
complete lock set is held, handoff reloads and revalidates typed normalization, hash, quality, operation contract,
provenance, retention and access policy; no value inspected before locking is used to create downstream state.
Concurrent mutation therefore commits
before linking or loses to immutable LINKED state. Migration 0010 first performs a value-free, fail-closed audit
of existing LINKED lineage. A validated legacy opaque Finnhub quote/EIA retail Evidence identity may be adopted
without rewriting it; newly created Evidence must satisfy the current operation-specific access policy.
Because PostgreSQL cannot safely reproduce the Python typed normalizers, deployment must run
`scripts/m2b_controlled_upgrade.py --writers-stopped` against revision 0009 and receive `DRY_RUN`, then use that
same entry with `--execute --writers-stopped`. It holds the maintenance lock, runs the typed validator, rejects
intervening state drift and applies exactly 0010. Bare production `alembic upgrade 0010` is prohibited. The
validator uses a repeatable-read, read-only bounded keyset scan and emits only counts and stable safe error codes;
0010 then performs the complementary relational fail-closed audit. Neither stage repairs factual data.
PostgreSQL integration tests create a random `news_collect_test_<token>` disposable database per pytest process,
bind the token to that exact database, migrate it, and drop only that database. A skipped PostgreSQL integration
module must be reported as skipped and is not a full PostgreSQL PASS.

## Acceptance

- Six typed operation packets, complete/partial quality, optional/no Content and exact numeric facts.
- Deterministic digest/order, multi-revision current selection and explicit revision/byte truncation.
- PostgreSQL direct-SQL immutability, tamper/provenance/hash/unknown-contract fail-closed behavior.
- Repeated/concurrent reads create no writes and never invoke the legacy placeholder mapper.
- R1/R2/R8-A/M2-A, scheduler and Telegram regressions remain green; Alembic stays single-head 0010.
