# SPEC-0047 — M2-C Event Evidence Bundle

Status: Active — Draft Implementation Review

## Frozen contract

M2-C is the first durable production consumer of `RichEvidencePacket`. It performs no AI, recommendation,
market validation or conflict adjudication. Production collection authority remains `legacy`.

### Identity and ownership

- `EventCandidate` owns event identity and active Evidence membership. M2-C never creates or changes clustering.
- If the last active association is removed, reconciliation removes only the mutable head and blocks the job with
  `event_bundle_no_active_evidence`; immutable revisions remain. A later reviewed association reopens the job and
  appends a new revision, preventing M2-D from consuming a stale historical head as current evidence.
- `EventEvidenceBundle` identity is `(event_candidate_id, revision)`. Revisions are append-only; a mutable head row
  points to the current revision and the latest READY canonical revision.
- `bundle_digest` is SHA-256 over canonical versioned material: event id, ordered item digests/relations, diversity,
  time range, status and reason codes. Reprocessing identical material creates no revision.
- Existing `event_candidate_evidence` rows remain the reversible membership history. One Evidence may belong to
  multiple Events; the same Event/Evidence pair has at most one active membership.

### Deterministic association relations

The relation is descriptive and never resolves truth:

1. `superseding`: the same active membership existed in the prior bundle and its packet digest changed.
2. `duplicate`: within one exact deterministic fact-identity group, an unchanged value group has more than one
   member; every member of that group is duplicate, avoiding an arbitrary UUID-based representative.
3. `contradicting`: within that same exact fact-identity group, an unchanged single-member value group coexists
   with another value group. V1 never compares different identities and performs no cross-Provider semantic
   adjudication. The precedence is superseding, duplicate, contradicting, supporting.
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
- All packets used by one revision are read from one repeatable-read snapshot. The commit transaction then verifies
  linked-projection count and current projection identity/hash; concurrent revision input makes the attempt RETRY
  and cannot publish a stale/mixed head or return unchanged.
- A concurrent membership change or not-yet-linked Rich Evidence dependency is RETRY, while invalid identity,
  provenance, packet contracts and exhausted retry are value-free BLOCKED outcomes.
- A claim token is revalidated under lock inside the same transaction that creates a revision, so a stale worker
  cannot create a bundle, advance the head or complete a recovered claim (ABA protection).
- Event membership authority itself is capped at 500 active associations per EventCandidate by a PostgreSQL
  advisory-lock trigger, including concurrent inserts/reactivation. Inactive history is not counted. Bundle and
  service bounds remain defense in depth.
- Retry exhaustion records a value-free dependency fingerprint. Unchanged input stays BLOCKED; a later Evidence
  link or association material change atomically reopens the job with a fresh finite retry budget.
- The mutable head may point only to the greatest revision and its canonical pointer must equal the greatest READY
  revision. PostgreSQL enforces these current/canonical semantics and rejects rollback/rebinding by SQL bypass.
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
concurrent/idempotent processing; claim-token ABA rejection; retry/stale recovery; SQL-bypass immutability,
500-member enforcement and latest-head guards; 0010→0011→0010→0011; existing
Phase 1/R1/R2/R8-A/M2-A/M2-B/scheduler/Telegram regressions; clean archive review.
