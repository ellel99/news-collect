# SPEC-0039 — Phase 1 Acceptance + Event Candidate Foundation

Status：Active — Docs Review / Foundation Freeze Review required

Phase：Phase transition — Phase 1 technical acceptance to Event Intelligence

Foundation：v2.1-FROZEN remains effective；v2.2 transition draft pending Freeze Review

Depends on：SPEC-0018–0021、SPEC-0023–0038（Completed）

## 1. Objective and authorization boundary

SPEC-0039 records Phase 1 core technical acceptance and defines one bounded Event Candidate foundation:

`EvidenceItem + ContentItem → deterministic pre-dedup → EventCandidate ↔ Evidence provenance`

It also defines provider/model-neutral importance and `ImpactAnalyzer` contracts. The user authorized the
combined docs-and-implementation scope, but repository governance requires a Foundation Freeze Review and
Docs Review before migration or Python implementation. Therefore this revision is documentation and
governance preparation only. Implementation begins only after both reviews explicitly PASS.

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
- EvidenceItem remains the factual/provenance foundation; EventCandidate never replaces or deletes RawItem,
  EvidenceItem, or ContentItem.
- EventCandidate grouping must be deterministic, explainable, idempotent, reversible, and provider-neutral.
- AI may only enter behind an `ImpactAnalyzer` contract after Event facts and evidence are assembled.
- This SPEC permits a mock/deterministic analyzer only; no real LLM, recommendation, market-validation
  runtime, portfolio action, or trading semantics.

The proposal is recorded in `docs/FOUNDATION_V2_2_DRAFT.md` and D-025. v2.1-FROZEN remains authoritative
until Freeze Review PASS.

## 4. SPEC-0022 traceability

The candidate `SPEC-0022 Dedup and Event Candidate Layer` is **absorbed/superseded by SPEC-0039 pending
review**. Reused design intent:

- deterministic dedup before Event creation;
- Evidence provenance preservation;
- Event candidate boundary after evidence persistence;
- Foundation revision dependency.

SPEC-0039 adds Phase 1 acceptance, explicit transition governance, persistence/idempotency requirements,
importance scoring, and the ImpactAnalyzer contract. SPEC-0022 must not become a competing Active SPEC.

## 5. Planned deterministic pre-dedup

Inputs are approved internal EvidenceItem/ContentItem projections, never provider SDK objects or raw payloads.
Candidate keys use the strongest available identity in order:

1. provider-scoped official identity (for example SEC accession);
2. provider + stable external ID for exact same-provider identity;
3. normalized canonical URL;
4. normalized title fingerprint constrained by shared entity/asset and a bounded publication time window;
5. existing content/provider hash only when identity constraints remain fail-closed.

Rules:

- exact official/provider identity is idempotent;
- cross-provider grouping requires canonical URL or entity + title fingerprint + time-window agreement;
- same company alone never merges events;
- missing/ambiguous identity creates separate candidates rather than broad merging;
- rerunning the same inputs yields the same candidate identity;
- no RawItem/EvidenceItem is deleted or overwritten.

No embeddings, vector database, LLM clustering, or semantic similarity is included.

## 6. Planned persistence contract

Implementation may introduce one isolated, reversible Alembic revision after approval:

### `event_candidates`

- UUID `id` primary key;
- deterministic `cluster_key` unique and non-null;
- `event_type`, lifecycle `status`;
- optional safe `canonical_title` / `fact_summary`;
- `first_seen_at`, `latest_seen_at`, optional `occurred_at` / `published_at`;
- provider-neutral entity/company/asset/sector/topic reference arrays;
- `evidence_count`, `source_count`, `confidence`, `importance_score` with DB checks;
- timestamps and indexes for status/time/entity lookup.

### `event_candidate_evidence`

- `event_candidate_id` FK;
- `evidence_item_id` FK with unique membership;
- official-source flag and added timestamp;
- deletion policy must preserve EvidenceItem provenance.

Required invariants:

- many EvidenceItems may support one EventCandidate;
- every EventCandidate is traceable to EvidenceItem → RawItem → Source/Provider;
- a repeated run cannot create another candidate or duplicate association;
- persistence failures are explicit and do not mutate evidence;
- migration upgrade/downgrade/re-upgrade must pass on PostgreSQL 16.

## 7. Minimal deterministic clustering

Level 1 only:

- exact identities and canonical URL are strong matches;
- entity + normalized title fingerprint requires a bounded time window;
- official filing and coverage may group only when identity/entity/time/fingerprint constraints agree;
- time-window expiry or conflicting identity fails closed into separate candidates;
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

- [ ] Migration upgrade/downgrade/re-upgrade on PostgreSQL 16.
- [ ] Same provider/external ID and same official identity are idempotent.
- [ ] Same canonical URL across Providers produces one candidate.
- [ ] Official filing + coverage groups only with entity/time/fingerprint agreement.
- [ ] Same company but different fact remains separate; expired window remains separate.
- [ ] Repeated processing creates neither duplicate EventCandidate nor duplicate association.
- [ ] Multiple EvidenceItems map to one EventCandidate with full provenance traceability.
- [ ] Importance scoring is deterministic, bounded, and exposes component reasons.
- [ ] ImpactAnalyzer mock validates all direction/horizon values and rejects trading-action language.
- [ ] Tests/CI make no real Provider, Telegram, market-validation, or AI request.
- [ ] Existing Phase 1 collection/evidence/scheduler/Telegram regressions remain PASS.
- [ ] Reviewer approves Foundation v2.2 transition draft and this Docs Review.

## 11. Explicit non-goals

- No real LLM/API, embedding/vector DB, or complex semantic clustering.
- No BUY/SELL/HOLD, target price, portfolio advice, execution, or broker integration.
- No Market Validation runtime, new Provider, X, streaming/webhook/event-bus infrastructure.
- No Phase 1 scheduler rewrite or provider raw payload propagation.
- No SPEC-0040 work.

## 12. Review history

| Round | Result | Evidence | Resolution |
|---|---|---|---|
| 1 | PENDING | Phase 1 evidence inventory, D-025 and v2.2 transition draft, bounded implementation contract | Await Foundation Freeze Review and SPEC Docs Review |
