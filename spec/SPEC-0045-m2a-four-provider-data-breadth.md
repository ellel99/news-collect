# SPEC-0045 — M2-A Four-Provider Data Breadth and Bounded Collection

Status: Active — Implementation Review

## Approved scope

The user's 2026-08-26 execution contract authorizes code, a forward schema migration with an explicit
stopped-writer deployment boundary, synthetic tests and one Draft PR. Migration 0009 is not rolling-upgrade
compatible and is not classified as additive/expand-only.
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

Every v2 config requires window_mode: fixed_window with explicit start/end for manual bounded collection,
or rolling_window with lookback_seconds, overlap_seconds, ingestion_lag_seconds and operation-specific
granularity (news/SEC day, RTO hour, retail month). Dynamic start/end never enter target config. News/SEC
ceiling is 31 days, RTO 7 days. Retail uses lookback_months (1–12), overlap_months (less than lookback),
ingestion_lag_months (0–12), month granularity and durable monthly watermark; fixed retail bounds are at most 12
inclusive calendar periods. Monthly config cannot substitute second-based parameters. `lookback_months=N` yields
exactly N complete periods; lag zero ends at the previous complete month. Overlap subtracts the configured period
count from an in-window watermark. A future/after-window watermark clamps to the last complete period and never
reverses the window.
Automatic target scheduling skips v2 fixed_window targets; only rolling_window participates in normal cadence.
The same bounded worker remains usable for explicitly dispatched manual fixed-window collection.

Before the first Provider request, the worker atomically freezes start/end in CollectionRun.resolved_window and
persists a pending continuation recovery record. Exact lineage binds target/config revision, operation,
operation-config/provider-contract versions, cursor version and run mode. Retry, crash, lock-loss and stale/new-run
recovery reuse it; mismatched lineage fails before network. Database guards enforce immutable run bounds and exact
v2 continuation identity. Successful completion clears recovery state so the next cadence resolves a new window.

Provider pagination is not a transactional upstream snapshot. Reconciliation repeats the configured bounded
window to discover late revisions. Finnhub continuation stores last (published_at, provider_item_id); SEC
stores last (filing_date, accession_number) per file, explicit current file and bounded remaining file queue.
Only greater keys emit during a continuation. Append/reorder/revision cannot invalidate a whole snapshot;
earlier arrivals and revisions to emitted keys are rediscovered by the next overlap reconciliation.
No mandatory array fingerprint, fabricated provider page API or saved response supports continuation.

Missing Finnhub ID requires a validated public URL for fallback; no ID plus no URL fails closed, never hashes
None. Provider ID and normalized-URL fallback identities are provider-global and exclude target symbol. URL
normalization lowercases scheme/host, removes default ports/fragments/tracking parameters, sorts safe parameters,
and rejects userinfo or secret-bearing keys. Traceable invalid rows get a hash-only provider_row_rejected AuditLog,
deterministic per target/operation/
row identity. Marker, valid items and checkpoint commit together; repeats do not duplicate markers. Unknown
identity or untrusted page structure fails the whole page closed without silently skipping facts.

## Persistence and migration 0009

- Parent revision is 0008; single head 0009. No published migration is edited.
- Adds `finnhub_company_news` Evidence type and its news flags; database guards use provider + operation.
- Adds durable CollectionRun request_count/page_count for cumulative budgets across retry/restart.
- Adds CollectionRun.resolved_window with safe JSON constraint and once-initialized immutable-window trigger.
- Adds an exact v2 continuation database guard; operation codecs reject unknown/cross-operation fields, wrong
  config/window/lineage, invalid numeric/key state and cross-CIK SEC queues.
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
  Revision 0009 is a forward schema migration with an explicit stopped-writer deployment boundary; it is not
  rolling-upgrade compatible. Production rollout remains unauthorized.

## Rollback compatibility and production gates

New operations have no invented legacy cursor mapping. They cannot activate during the existing rollback window
unless a separately reviewed mapping exists; this PR does not bypass the active-identity constraint or execute
Migration B. Test targets are synthetic fixtures, not production activation evidence. Legacy authority and its
existing scheduler remain unchanged. Multi-target implementation does not imply readiness for production cutover.

Load/revise and shadow/preflight exact comparison require target.legacy_cursor_type == resolved contract mapping.
All v2 mappings are NULL. A v1 target cannot inherit legacy identity by upgrading version: use a distinct target.
v2 completion never writes legacy cursor. Local integration fixtures temporarily disable only the temporary
activation trigger in the disposable test DB, then restore it, to simulate future v2 eligibility. Production
retains that trigger; no Migration B is supplied. Tests use shared TEST_DATABASE_URL or CI's created database,
never a developer-specific database name.

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
