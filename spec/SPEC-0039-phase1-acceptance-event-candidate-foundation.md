# SPEC-0039 — Phase 1 Acceptance + Event Candidate Foundation

Status：Completed — Implementation Review approved

Phase：Phase transition — Phase 1 technical acceptance to Event Intelligence

Foundation：v2.2-FROZEN（approved 2026-08-13）

Depends on：SPEC-0018–0021、SPEC-0023–0038（Completed）

## 1. Objective and authorization boundary

SPEC-0039 records Phase 1 core technical acceptance and defines one bounded Event Candidate foundation:

`EvidenceItem + ContentItem → deterministic pre-dedup → EventCandidate ↔ Evidence provenance`

It also defines provider/model-neutral importance and `ImpactAnalyzer` contracts. Foundation Freeze Review
and SPEC Docs Review passed on 2026-08-13; final Implementation Review passed on 2026-08-13.

## 2. Phase 1 technical acceptance

Existing reviewed evidence is reused; no Provider or Telegram live request is repeated.

Completed core path:

- Marketaux, Finnhub, EIA Open Data, and SEC EDGAR provider-neutral adapters and bounded runtime paths.
- CollectionRunner, cursor/checkpoint, retry/backoff, stale-run recovery, and provider isolation.
- RawItem → CommonEvidenceEnvelope → evidence_items provenance-safe write path.
- metadata-only ContentItem projections and visible feed.
- provider cadence scheduler and Telegram routing with Notification dedup/retry/recovery.
- PostgreSQL migrations, schema constraints, mocked tests, runtime verification, and secret-safe output.

Acceptance result：**Phase 1 core Information Collection & Push technical path PASS**.

Deferred capabilities are not silently declared complete: X source/account collection (SPEC-0005), full
backup/restore operational exercise, management Bot, and broader operations acceptance remain independent
follow-up capabilities. Their deferral does not authorize Event implementation before the transition review.

## 3. Foundation transition proposal

Minimal transition requested for v2.2:

- Phase 1 Content First remains immutable historical behavior and its pipeline stays operational.
- The next active engineering stage becomes Event Intelligence / Event First.
- RawItem remains the original collection trace/provenance layer. EvidenceItem is the factual/provenance
  authority for Event Intelligence. ContentItem remains a content-safe display/projection layer and may be a
  safe deterministic clustering input, but is not Event factual authority. EventCandidate is additive and
  never replaces, deletes, or overwrites any of these layers.
- EventCandidate grouping must be deterministic, explainable, idempotent, reversible, and provider-neutral.
- AI may only enter behind an `ImpactAnalyzer` contract after Event facts and evidence are assembled.
- This SPEC permits a mock/deterministic analyzer only; no real LLM, recommendation, market-validation
  runtime, portfolio action, or trading semantics.

The approved transition is frozen in `docs/FOUNDATION_V2_2.md` and D-025. v2.1 safety and trading-action
boundaries continue under v2.2-FROZEN.

## 4. SPEC-0022 traceability

The candidate `SPEC-0022 Dedup and Event Candidate Layer` is **absorbed/superseded by SPEC-0039**. Reused
design intent:

- deterministic dedup before Event creation;
- Evidence provenance preservation;
- Event candidate boundary after evidence persistence;
- Foundation revision dependency.

SPEC-0039 adds Phase 1 acceptance, explicit transition governance, persistence/idempotency requirements,
importance scoring, and the ImpactAnalyzer contract. SPEC-0022 must not become a competing Active SPEC.

## 5. Planned deterministic pre-dedup

Inputs are approved internal EvidenceItem data plus content-safe ContentItem projections, never provider SDK
objects or raw payloads. EvidenceItem remains authoritative when display projection and evidence disagree.

Every new candidate receives one deterministic **stable anchor** and derives `cluster_key` from that anchor.
Anchor priority is:

1. provider-scoped official identity (for example SEC accession);
2. normalized canonical URL;
3. stable provider + external identity for exact same-provider identity;
4. normalized title fingerprint constrained by shared entity/asset and a bounded publication time window;
5. existing content/provider hash only when identity constraints remain fail-closed.

Rules:

- canonical URL normalization preserves all business query parameters, sorts query pairs deterministically,
  normalizes scheme/hostname/default port/path, ignores fragments, and removes only the explicit tracking
  allowlist (`utm_*`, `fbclid`, `gclid`); same path with different business query values must not merge;
- exact official/provider identity is idempotent;
- cross-provider grouping requires canonical URL or entity + title fingerprint + time-window agreement;
- same company alone never merges events;
- same ticker alone and a wide time window never merge events;
- if incoming Evidence cannot stably match an existing candidate, create a new EventCandidate instead of
  forcing a broad merge;
- missing/ambiguous identity creates separate candidates rather than broad merging;
- rerunning the same inputs yields the same candidate identity;
- no RawItem/EvidenceItem is deleted or overwritten.

Candidate identity rules:

- after creation, `EventCandidate.id` and `cluster_key` are immutable;
- `cluster_key` is derived from the stable initial anchor, never from the current complete Evidence membership
  set, so adding/removing an association cannot change identity;
- later matching Evidence only adds an association to the existing EventCandidate;
- a later stronger official identity may enrich a separate canonical/strong-identity field, but must not rewrite
  `cluster_key`, create a second candidate for the same accepted grouping, or destroy historical identity;
- concurrent creation relies on the DB unique `cluster_key` plus a transactional insert/upsert-or-reload path;
- identical inputs, including concurrent/repeated processing, resolve to the same EventCandidate.

No embeddings, vector database, LLM clustering, or semantic similarity is included.

## 6. Planned persistence contract

Implementation may introduce one isolated, reversible Alembic revision after approval:

### `event_candidates`

- UUID `id` primary key;
- deterministic `cluster_key` unique and non-null;
- immutable stable-anchor type/value (or their opaque deterministic representation) plus optional stronger
  canonical identity enrichment; neither enrichment nor membership changes may rewrite `cluster_key`;
- `event_type`, lifecycle `status`;
- optional safe `canonical_title` / `fact_summary`;
- `first_seen_at`, `latest_seen_at`, optional `occurred_at` / `published_at`;
- provider-neutral entity/company/asset/sector/topic reference arrays;
- `evidence_count`, `source_count`, `confidence`, `importance_score` with DB checks;
- timestamps and indexes for status/time/entity lookup.

### `event_candidate_evidence`

- independent UUID association-generation `id` primary key;
- `event_candidate_id` FK;
- `evidence_item_id` FK;
- partial unique index on `(event_candidate_id, evidence_item_id) WHERE active = true` prevents duplicate
  active membership while allowing multiple immutable inactive history generations;
- this first version does **not** add `UNIQUE(evidence_item_id)`: single-event ownership is not assumed without
  an independently reviewed policy;
- `match_rule` / `cluster_rule`, `rule_version`, official-source flag, and `added_at` explain every association;
- minimal reversible history uses append-only generations with `active` plus nullable `removed_at`; deactivate
  preserves the old row, reactivation creates a new row, and regrouping deactivates the old candidate link then
  adds a new target link without overwriting prior match rule/version/timestamps;
- physical EvidenceItem deletion is never used to correct clustering, and association rows are not hard-deleted
  during normal regrouping.

Required invariants:

- many EvidenceItems may support one EventCandidate;
- every EventCandidate is traceable to EvidenceItem → RawItem → Source/Provider;
- a repeated run cannot create another candidate or duplicate association;
- candidate creation is concurrency-safe through unique `cluster_key` and transactional conflict handling;
- each active or historical association explains its rule/version and supports future reversible regrouping;
- repository lookup returns every active membership; more than one active candidate for the same Evidence is an
  explicit ambiguity that fails closed without modifying candidates, creating another candidate, or dropping links;
- evidence/source counts, confidence, importance and reasons describe active membership only and are recalculated
  after create, deactivate, reactivate and regroup;
- a candidate with no active Evidence remains as an auditable `rejected` candidate with evidence/source counts,
  confidence and importance all zero; reactivation restores candidate status and active-only aggregates;
- persistence failures are explicit and do not mutate evidence;
- migration upgrade/downgrade/re-upgrade must pass on PostgreSQL 16.

## 7. Minimal deterministic clustering

Level 1 only:

- exact identities and canonical URL are strong matches;
- entity + normalized title fingerprint requires a bounded time window;
- official filing and coverage may group only when identity/entity/time/fingerprint constraints agree;
- time-window expiry or conflicting identity fails closed into separate candidates;
- absence of a stable match creates a new EventCandidate; same company/ticker alone is never sufficient;
- each decision exposes the rule/key used, without raw content or secrets.

AI-assisted clustering is interface-only future work and is not implemented here.

## 8. Importance scoring foundation

Define a replaceable `ImportanceScorer` protocol plus deterministic first implementation. Inputs:

- official-source presence;
- evidence/source count and source diversity;
- explicit source priority;
- entity relevance and recency;
- corroboration and confidence.

Output is a bounded score and component reasons. It is not an investment score and cannot emit BUY/SELL/HOLD,
target price, position sizing, or rebalance language.

## 9. ImpactAnalyzer contract

Provider/model-neutral input:

- EventCandidate identity and fact summary;
- evidence summaries/provenance references;
- entities, companies, assets, sectors, topics;
- uncertainty, contradictions, freshness, official/corroborating evidence.

Output contract:

- affected companies/assets/sectors;
- direction: positive / negative / mixed / uncertain;
- horizon: immediate / short_term / medium_term / long_term;
- impact channels, confidence, rationale summary, uncertainty;
- `required_market_validation` and `analysis_version`.

Only a deterministic mock/stub and validation tests are permitted in this SPEC. No real AI call, SDK, prompt,
model credential, generated recommendation, or Telegram AI delivery.

## 10. Planned tests and acceptance

- [x] Migration upgrade/downgrade/re-upgrade on PostgreSQL 16.
- [x] Same provider/external ID and same official identity are idempotent.
- [x] EventCandidate id/cluster_key remain unchanged when later Evidence is associated or stronger identity enriches it.
- [x] Concurrent candidate creation resolves through DB uniqueness to one stable identity.
- [x] Same canonical URL across Providers produces one candidate.
- [x] Different business query values remain distinct; allowlisted tracking-only differences normalize equally.
- [x] Official filing + coverage groups only with entity/time/fingerprint agreement.
- [x] Same company but different fact remains separate; expired window remains separate.
- [x] Repeated processing creates neither duplicate EventCandidate nor duplicate association.
- [x] Multiple EvidenceItems map to one EventCandidate with full provenance traceability.
- [x] Active association generation uniqueness is pair-scoped; append-only rule/version and active/removed
  history make regrouping auditable.
- [x] Deactivate/reactivate/regroup refresh active-only aggregates; zero-active candidates remain auditable.
- [x] Multiple active candidate associations for one Evidence fail closed without mutation or data loss.
- [x] Importance scoring is deterministic, bounded, and exposes component reasons.
- [x] ImpactAnalyzer mock validates all direction/horizon values and rejects trading-action language.
- [x] Tests make no real Provider, Telegram, market-validation, or AI request.
- [x] Existing Phase 1 collection/evidence/scheduler/Telegram regressions remain PASS.
- [x] Reviewer approved the Foundation v2.2 transition and SPEC-0039 Docs Review.

## 11. Explicit non-goals

- No real LLM/API, embedding/vector DB, or complex semantic clustering.
- No BUY/SELL/HOLD, target price, portfolio advice, execution, or broker integration.
- No Market Validation runtime, new Provider, X, streaming/webhook/event-bus infrastructure.
- No Phase 1 scheduler rewrite or provider raw payload propagation.
- No SPEC-0040 work.

## 12. Review history

| Round | Result | Evidence | Resolution |
|---|---|---|---|
| 1 | REQUEST CHANGES | Layer authority, stable candidate identity, and reversible association semantics required clarification | Clarified in commit 4092f6a |
| 2 | PASS | Foundation v2.2 Freeze Review and SPEC-0039 Docs Review | Bounded implementation authorized in this PR |
| 3 | REQUEST CHANGES | Canonical URL false-merge risk, overwritten association history, stale aggregates, active-membership ambiguity, and stale docs | Corrected in the next PR #38 revision |
| 4 | PASS | Conservative URL identity, append-only membership generations, active aggregate/regroup semantics, 444-test regression, migration round trip and package evidence | Implementation Review approved; SPEC completed |
