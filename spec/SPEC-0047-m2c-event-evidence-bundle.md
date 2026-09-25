# SPEC-0047 — M2-C Event Evidence Bundle

Status: Active — Draft Implementation Review

## Frozen contract

M2-C is the first durable production consumer of `RichEvidencePacket`. It performs no AI, recommendation,
market validation or conflict adjudication. Production collection authority remains `legacy`.

### Identity and ownership

- `EventCandidate` owns event identity and active Evidence membership. M2-C never creates or changes clustering.
- `EventEvidenceBundle` identity is `(event_candidate_id, revision)`. Revisions are append-only; a mutable head row
  points to the current revision and the latest READY canonical revision.
- `bundle_digest` is SHA-256 over canonical versioned material: event id, ordered item digests/relations, diversity,
  time range, status and reason codes. Reprocessing identical material creates no revision.
- Existing `event_candidate_evidence` rows remain the reversible membership history. One Evidence may belong to
  multiple Events; the same Event/Evidence pair has at most one active membership.

### Deterministic association relations

The relation is descriptive and never resolves truth:

1. `superseding`: the same active membership existed in the prior bundle and its packet digest changed.
2. `duplicate`: another item in the same bundle has the same deterministic fact-identity digest and fact-value
   digest.
3. `contradicting`: another item has the same fact-identity digest but a different fact-value digest.
4. `supporting`: every other valid active association.

Fact identity/value material is operation-specific, typed and derived only from `RichEvidencePacket`; raw payload
and provider SDK objects are forbidden. Conflict is expressed, not judged.

### Revision, diversity and state

- Every revision retains its immutable ordered items and source/provider/operation coverage.
- Source diversity is the count of distinct source identities; provider and operation coverage are sorted unique
  allowlisted strings. Time range is min/max packet current publication time.
- READY requires only complete packets and no truncation. PARTIAL records partial/truncated input with stable
  reason codes. BLOCKED is a durable job outcome for missing membership, invalid packet/provenance or retry
  exhaustion; a BLOCKED job does not create a bundle revision.
- M2-D consumes only a bundle referenced by the head, verifies `bundle_version`, `bundle_digest`, READY/PARTIAL
  state, ordered immutable items and canonical packet digests, and never reads provider raw payload.

### Runtime and migration

- An authority-neutral Celery reconciliation task discovers EventCandidates with active associations, claims jobs
  with `FOR UPDATE SKIP LOCKED`, and performs bounded keyset/batch processing.
- Jobs implement PENDING/PROCESSING/READY/PARTIAL/RETRY/BLOCKED, finite retry and stale recovery. Per-event bundle,
  items and head update are one transaction and idempotent under unique constraints.
- A claim token prevents a stale worker from completing a recovered claim. One bundle is capped at 500 active
  Evidence memberships; exceeding the cap is a value-free BLOCKED outcome rather than an unbounded scan.
- Migration 0011 is additive. Existing-state preflight rejects broken active membership value-free. Downgrade is
  allowed only when bundle/job/head tables are empty; it never removes EventCandidate/Evidence history.
- `scripts/m2c_pre_migration_validator.py` is the read-only controlled preflight. It reports counts and stable
  reason codes only; it never repairs, migrates or reveals Evidence values. Periodic discovery uses a durable UUID
  keyset marker so more than one batch cannot starve later eligible EventCandidates.

## Explicit exclusions

No AI association or conflict judgment, cheap/strong model, investment recommendation, production migration,
authority activation/cutover, backfill/historical replay, Provider/Telegram/AI request, Event reclustering,
Fact/ImpactAnalysis or M2-D implementation.

## Acceptance

Deterministic identity/relation/digest tests; append-only revision and canonical/current semantics; multi-source
diversity/time coverage; duplicate/contradicting/superseding expression; bounded pagination and no starvation;
concurrent/idempotent processing; retry/stale recovery; SQL-bypass immutability; 0010→0011→0010→0011; existing
Phase 1/R1/R2/R8-A/M2-A/M2-B/scheduler/Telegram regressions; clean archive review.
