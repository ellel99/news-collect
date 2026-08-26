# M2-A Implementation Review Package

Baseline: main `f398f5557a41ebab6d2ba701f1bdb618d1106bc7`; Foundation v2.3-FROZEN.
Review status: PENDING. Production authority: legacy. PR #39: untouched Draft.

Review together: SPEC-0044 milestone, SPEC-0045 implementation, M2 operation matrix, migration 0009,
BreadthAdapter/config, operation policy, registry/factory, CollectionControlPlaneWorker page transactions,
RawItemObservation page identity, R2 typed contracts, R8-A handoff and Notification eligibility.

Acceptance must trace request budgets → page/array continuation → transaction checkpoint → canonical RawItem →
page observation → SafeFactProjection → revalidation → canonical Evidence link. No numeric count or presence flag
is substituted for stored numeric facts. Canonical Evidence/Content are never overwritten by revisions.

Known boundaries: v1 remains compatibility-only; windows are explicit rather than automatically rolling; API
pagination cannot promise an immutable upstream snapshot; array resume fails closed on changed response content.
Production deployment requires separate stopped-writer review because the observation uniqueness conflict target
changes. No activation is possible merely by selecting an environment setting. No M2-B/C/D output is implemented.

Validation results and exact reviewed commit/changed-file count are recorded in the Draft PR body after execution.

## Implementation self-check ledger (independent review still pending)

- VERIFIED_FIXED: provider-only Evidence dispatch now resolves exact operation/version; company-news has its
  own news type and RTO has its own typed factual contract.
- VERIFIED_FIXED: continuation checkpoints commit with each page; request/page counters survive retry; overlap
  retains separate page observations without rewriting canonical RawItem/Evidence/Content.
- VERIFIED_FIXED: SEC history references are same-CIK allowlisted names; changed array snapshots fail closed;
  downgrade refuses incompatible operation/observation state.
- INTENTIONAL_BOUNDARY: explicit frozen windows, bounded responses/requests, blocked body/summary fields,
  v1 compatibility paths, legacy authority and no production activation.
- DEFERRED: linked-payload immutability enhancement/Rich Evidence Packet (M2-B), Event Bundle (M2-C), machine
  readiness gate (M2-D), and separately authorized stopped-writer deployment/production verification.

Coverage follows the repository review protocol: contracts/config → request/continuation → persistence and
constraints → handoff/content/notification policy → retry/restart/downgrade → regression and docs. Existing
R1/R2/R8-A concurrency, stale recovery and scheduler/Telegram tests remain required alongside new M2-A tests.
Real provider entitlement, live coverage and activation are not demonstrated by synthetic tests.
