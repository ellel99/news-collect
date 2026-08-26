# SPEC-0045 — M2-A Four-Provider Data Breadth and Bounded Collection

Status: Active — Implementation Review

## Approved scope

The user's 2026-08-26 execution contract authorizes code, additive migration, synthetic tests and one Draft PR.
It does not authorize deployment or external requests. R8-A completed in merged PR #45. Current production
authority remains legacy; new v2 targets are not automatically created or activated.

## Operation contract

See [operation/cursor matrix](../docs/M2_PROVIDER_OPERATION_MATRIX.md). The registry resolves exact provider,
operation, config version and contract version. v1 operations retain their reviewed compatibility semantics;
expanded operations use config/contract version 2 and independent bounded-window continuation version 1.
Unknown tuples fail closed. No provider fallback, dynamic endpoint, credential-bearing config or class path.

Each target supplies its own operation config, cadence, revision, request/page/runtime/byte limits. Each adapter
fetch sends at most one request. A run reserves its durable request count before the send; retry does not reset
the budget. Page persistence atomically commits canonical RawItem, observation, PENDING SafeFactProjection and
target-owned continuation. No page is advanced before that transaction commits. Budget exhaustion is durable
PARTIAL/coverage_incomplete; the next authorized run resumes the saved continuation.

Windows are explicit and bounded, not unlimited backfill. They stay frozen during a run and resume. Changing
window/config requires existing config_revision CAS/review; a mismatched continuation fails closed rather than
silently skipping. The current implementation does not automatically slide or activate windows.

Provider pagination is not a transactional upstream snapshot. Reconciliation repeats the configured bounded
window to discover late revisions. Array-only Finnhub company-news and SEC submissions responses use a stable
response fingerprint and local offset; resume refuses changed snapshots. This is explicitly not a fabricated
provider page API. No full response is stored to support continuation.

## Persistence and migration 0009

- Parent revision is 0008; single head 0009. No published migration is edited.
- Adds `finnhub_company_news` Evidence type and its news flags; database guards use provider + operation.
- Adds durable CollectionRun request_count/page_count for cumulative budgets across retry/restart.
- Adds RawItemObservation.observation_key (default `run` for legacy), changing uniqueness to
  `(collection_run_id, raw_item_id, observation_key)`. v2 key is a hash of the incoming continuation, so page replay
  is idempotent and the same canonical item observed on another page retains a separate observation/projection.
- Existing raw/evidence/content rows are not rewritten. RawItem.collection_run_id remains first persistence.
- No factual payload is copied into Evidence. company_news may create safe ARTICLE Content; quote and all EIA
  observations never create Content. SEC stays official link-only and body unavailable.
- New policy can select only company_news ARTICLE content for Notification intent, never quote/EIA fake news;
  no push or cutover is executed in this PR.
- Downgrade rejects v2 targets, new operation facts or page observation state. The revised observation conflict
  key requires matched application deployment; old pre-M2 binaries must not write through migration 0009.
  Production rollout remains unauthorized and requires a stopped-writer deployment review, not a rolling upgrade.

## Rollback compatibility and production gates

New operations have no invented legacy cursor mapping. They cannot activate during the existing rollback window
unless a separately reviewed mapping exists; this PR does not bypass the active-identity constraint or execute
Migration B. Test targets are synthetic fixtures, not production activation evidence. Legacy authority and its
existing scheduler remain unchanged. Multi-target implementation does not imply readiness for production cutover.

## Hardcode classification

| Class | Items | Disposition |
|---|---|---|
| A: removed in expanded path | fixed Marketaux page/query/limit3; one request/page; SEC recent-only; provider-only Evidence dispatch; single EIA series family | Explicit v2 windows, pages, budgets, configs, RTO and company-news contracts |
| B: intentional safety | quote limit1; manual live smoke limit1–3; no bodies; allowlisted routes; max20 requests/pages; response/runtime limits; legacy v1 regression defaults | Retained, not production breadth claims |
| C: later milestone | numeric presence/count fields as rich facts; complete content inference; Packet/Bundle/readiness/AI | Existing fields are compatibility metadata only; M2-B must read SafeFactProjection values, never reconstruct zeros |

Fixed example symbols/query/facets are examples, not the runtime allowlist. v2 production configuration comes
only from reviewed CollectionTarget.operation_config, never arbitrary environment activation. Legacy adapters
remain called by compatibility/smoke paths and are not dead code. No unrelated dead-path removal is performed.

## Safety and verification

No raw response persistence; Marketaux description/snippet and Finnhub summary/body remain blocked. No SEC
document download, arbitrary EIA endpoint, infinite history, live request, credential read, activation/cutover,
Migration B, Rich Evidence Packet, Event Bundle or AI. PR #39 remains untouched.

Tests must cover operation-specific safe facts, multi-page persistence, page failure/resume, page dedup with
observation lineage, SEC recent/history overlap and unsafe reference rejection, new Content policy, finite
budgets, unsupported versions, R1/R2/R8-A and scheduler/Telegram regression. Migration round trip is isolated.
