# M2-A Implementation Review Package

Baseline: main `f398f5557a41ebab6d2ba701f1bdb618d1106bc7`; Foundation v2.3-FROZEN.
Review status: PENDING. Production authority: legacy. PR #39: untouched Draft.

Review together: SPEC-0044 milestone, SPEC-0045 implementation, M2 operation matrix, migration 0009,
BreadthAdapter/config, operation policy, registry/factory, CollectionControlPlaneWorker page transactions,
RawItemObservation page identity, R2 typed contracts, R8-A handoff and Notification eligibility.

Acceptance must trace request budgets → page/array continuation → transaction checkpoint → canonical RawItem →
page observation → SafeFactProjection → revalidation → canonical Evidence link. No numeric count or presence flag
is substituted for stored numeric facts. Canonical Evidence/Content are never overwritten by revisions.

Known boundaries: v1 remains compatibility-only; fixed/manual or rolling windows are explicitly configured;
upstream pagination is not a transactional snapshot; keysets plus bounded overlap handle changing arrays.
Production deployment requires separate stopped-writer review because the observation uniqueness conflict target
changes. No activation is possible merely by selecting an environment setting. No M2-B/C/D output is implemented.

Validation results and exact reviewed commit/changed-file count are recorded in the Draft PR body after execution.

## Implementation self-check ledger (independent review still pending)

- VERIFIED_FIXED: provider-only Evidence dispatch now resolves exact operation/version; company-news has its
  own news type and RTO has its own typed factual contract.
- VERIFIED_FIXED: continuation checkpoints commit with each page; request/page counters survive retry; overlap
  retains separate page observations without rewriting canonical RawItem/Evidence/Content.
- VERIFIED_FIXED: SEC history references are same-CIK allowlisted names; per-file keysets survive array changes;
  downgrade refuses incompatible operation/observation state.
- INTENTIONAL_BOUNDARY: explicit frozen windows, bounded responses/requests, blocked body/summary fields,
  v1 compatibility paths, legacy authority and no production activation.
- DEFERRED: linked-payload immutability enhancement/Rich Evidence Packet (M2-B), Event Bundle (M2-C), machine
  readiness gate (M2-D), and separately authorized stopped-writer deployment/production verification.

Coverage follows the repository review protocol: contracts/config → request/continuation → persistence and
constraints → handoff/content/notification policy → retry/restart/downgrade → regression and docs. Existing
R1/R2/R8-A concurrency, stale recovery and scheduler/Telegram tests remain required alongside new M2-A tests.
Real provider entitlement, live coverage and activation are not demonstrated by synthetic tests.

## Directed blocker revision

Original c4c1313 push/PR quality checks both FAILED: developer-only database fallback. This is historical FAIL,
not pending/PASS. Revised tests use shared TEST_DATABASE_URL or the CI-created database.

The revision adds exact legacy mapping checks at load/revise/shadow, NULL v2 identities, append/reorder/revision
keysets, fixed/rolling run-frozen windows, URL-required fallback and atomic/idempotent rejected-row AuditLogs.
Run-frozen windows now have pre-request durable recovery state with exact target/config/operation/contract/cursor/
run-mode lineage and a PostgreSQL fail-closed guard bound to the exact run, immutable resolved window and config
hash. Legal empty completion clears that binding atomically; crash/retry/PARTIAL retain it. Continuation codecs are
exact per operation; SEC required column arrays and history reference name/from/to metadata are strictly validated.
Monthly EIA fixed windows require canonical `YYYY-MM-01` bounds and are inclusive complete-period sets:
`lookback_months=N` means exactly N periods and lag zero excludes the current incomplete month.
PostgreSQL integration proves provider-global Finnhub identity across symbol contexts: one canonical RawItem,
two observation/projection lineages, one Evidence/Content and at most one notification intent. It now drives two
same-account targets through CollectionControlPlaneWorker, including isolated retry and coexisting normal/backfill
cursors. Legacy account/type uniqueness applies only to target-less rows; target identity includes version/mode,
and downgrade fails before restoring the old index if collisions exist. Ordinary revision locks all target cursors
and rejects pending continuation.

PostgreSQL validates exact state values and exact RUNNING/PARTIAL/FAILED lineage, rejects SUCCEEDED binding and
request-late window/config freezing. All six operation paths collapse same-identity/same-projection rows and reject
conflicting same-page projections non-retryably before checkpoint advance. Six operation paths
prove traceable invalid-row isolation without turning out-of-scope filters into rejection markers.

Final review remediation aligns EIA preflight/total/codec/SQL at 10,000,000, gives Marketaux page 1000 a durable
non-looping PARTIAL terminal outcome, and moves Finnhub/SEC identity grouping before local `limit` slicing. Python
and SQL share the exact SEC null/string file matrix. A dedicated PAUSED-only continuation abandon operation uses
locked CAS tokens and value-free AuditLog records; ordinary revise remains fail closed. Migration and ORM metadata
both declare the window/config pair constraint.
Test-only future-v2 eligibility temporarily disables and restores the activation trigger in the disposable DB;
production constraints remain intact. Migration 0009 remains a stopped-writer boundary, not rolling-upgrade
compatible. Independent review is PENDING; exact final test count/HEAD/CI are recorded in the PR body.
