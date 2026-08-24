# R1 Unified Production Collection Control Plane — Docs Review Package

Status：COMPLETED — Reviewer decision PASS（docs-only，2026-08-14）

Foundation：v2.3-FROZEN

R0：Completed / PASS

Active SPEC：SPEC-0041 Implementation — Active — Implementation Review

Implementation：AUTHORIZED by explicit user command after this docs closeout merges；NOT STARTED in this PR

Baseline：`main@9c68dd6effe67d6f798fb080fdbffa6f80b77532`

Normative contract：`spec/SPEC-0041-implementation-unified-production-collection-control-plane.md`

## 1. Review decision requested

Reviewer is asked to accept or reject the implementation-ready R1 contract, not code. PASS would approve the
document only; a separate explicit implementation authorization is still required.

## 2. Final choices made

- `target_key` is globally unique and immutable; no source-scoped alternative remains.
- schema/adapter versions are separated from monotonic target `config_revision`; dispatch and worker require exact
  generation equality before credential/network.
- lifecycle uses one enum (`draft/active/paused/blocked/retired`), without a competing enabled flag.
- Source owns provider authorization/license/retention; SourceAccount owns optional identity; CollectionTarget owns
  typed operation, cadence, cursor/retry/run/health/dispatch identity and budgets.
- cursor becomes target-owned; CollectionRun receives mandatory target identity after deterministic migration.
- RawItem provenance uses its immutable run relation; no redundant `raw_items.target_id` is introduced.
- registry key and all four initial operation schemas are exact/static/versioned; unknown values fail closed.
- task payload carries identifiers only; credential resolution occurs only after worker DB reload.
- Notification is reused as durable Telegram state; Outbox is not turned into a competing delivery state machine and
  receives no R1 schema extension.
- all initial Provider operations have `pagination_capability=none`; `has_more` becomes explicit incomplete coverage,
  never a repeated first-page request or false completion.
- incomplete coverage persists as PARTIAL/`coverage_incomplete`, degraded health and unchanged complete watermark;
  the DB state—not transient logs—is restart authority.
- decoded response bytes are bounded in streaming transport before JSON parse or persistence.
- RawItem/Run source/account equality is PostgreSQL-enforced with null-safe semantics; migration mismatch blocks.
- scheduler and worker share one exact eligibility rule for Source, Account, Target, registry and revision state.
- only one scheduler is authoritative; shadow mode performs no request/enqueue/write.
- deployment is expand/contract: Migration A remains old/new-worker compatible, cursor rollback uses transactional
  dual-write, and Migration B is applied only after the reviewed rollback window closes.
- phases 0–3 continuously stop every legacy collection/stale-recovery writer; legacy authority resumes only through
  compatible runtime after zero RUNNING/null-unmapped rows and exact backfill-count verification.
- rollback ownership permits only one target across **all lifecycle states** per
  `(source_account_id,legacy_cursor_type)` through the temporary partial unique index. Draft, paused, blocked and
  retired owners retain the identity; pause, repair, resume and config revision never transfer it. Same-account
  multi-target operation begins only after Migration B removes this rollback-window restriction.
- `legacy_cursor_type varchar(100) NULL` is written only by migration-Phase-2 target INSERT from the static registry;
  every UPDATE is permanently forbidden and there is no repository initialization/transfer API.
- three DB objects have distinct lifecycles: Migration A creates and Migration B retains the permanent identity/
  provenance immutability trigger; Migration A creates and Migration B removes the temporary active non-null
  constraint; Migration A creates and Migration B removes the temporary rollback-ownership partial unique index.
- `Source.access_method` becomes permanently immutable once any target references the Source; pre-trigger migration
  scan blocks mismatches, while unreferenced Source follows existing management rules.
- PR #39 Draft migrations are excluded; implementation revision derives from the then-current real main head.

## 3. Review checklist

- [ ] Final fields, nullability, enums, checks, indexes, FKs and lifecycle are implementable.
- [ ] Permanent target/source/account/operation identity, INSERT-time legacy mapping, config and secret constraints
  are unambiguous and PostgreSQL-enforced; legacy identity is INSERT-time only with no UPDATE exception.
- [ ] Referenced Source provider identity is immutable and historical Run/RawItem provenance cannot drift.
- [ ] `config_revision` race, stale task, pause/resume and rollback semantics are accepted.
- [ ] Four Provider operation v1 schemas do not expand operation scope.
- [ ] cadence/cursor/lock/retry/run/health/dispatch ownership is target-specific.
- [ ] request/page/runtime/byte budgets and rate-limit grouping fail closed.
- [ ] response-byte enforcement occurs before JSON parse/persistence for both declared and streamed overflow.
- [ ] initial pagination capability is none; has_more records incomplete coverage without a second request.
- [ ] RawItem→Run→Target provenance is sufficient and consistency-protected.
- [ ] null-safe RawItem→Run provenance is DB-enforced and Content/Evidence audit is a R2/R8 prerequisite.
- [ ] collection remains independent from Notification/Telegram/Event delivery through durable PENDING intent,
  reconciliation and a delivery-only DB polling task.
- [ ] Notification candidate policy, cutover watermark, bounded reconciler and no-default-history rule are exact.
- [ ] recovery AuditLogs are append-only closed after intent recovery; no undefined per-Content override exists.
- [ ] legacy migration never auto-activates smoke defaults and ambiguous state blocks safely.
- [ ] Migration A compatibility, shadow/cutover, cursor dual-write rollback and Migration B finalization guarantee one
  authoritative scheduler and a deployable rolling sequence.
- [ ] DB-enforced rollback activation requires non-null account/type; rollback ownership is unique across every
  lifecycle and has no ordinary transfer API. Migration B removes the two temporary restrictions only after
  forward-recovery-only cutover and retains permanent immutability.
- [ ] the normative state matrix and config-revision in-flight state machine cover all terminal/retry/manual states.
- [ ] exact implementation files, five acceptance batches (four functional + Migration B) and test matrix are accepted.
- [ ] R2–R8, Provider expansion, Event/Evidence/Fact/AI and Market Validation remain out of scope.
- [ ] PR #39 remains Draft and untouched.
- [ ] `AI_CONTEXT.md` local protocol reference resolves to Git-tracked `docs/REVIEW_PROTOCOL.md` in a clean archive.

## 4. Non-actions in this PR

- No Python, Alembic revision, ORM/schema, runtime config or test behavior change.
- No Provider, Telegram or AI request; no credential/`.env` read.
- No live migration, target bootstrap, scheduler cutover or production activation.
- No R2 or later readiness work.

## 5. Reviewer result

Current result：**PASS — DOCS ONLY**（2026-08-14；reviewed head
`52d316029de1f1eb0264825819b84d3d639c060f`）.

The separate explicit user command dated 2026-08-14 authorizes R1 implementation to start only after this docs
closeout merges. This PR remains docs-only and does not itself contain implementation.

## 6. Validation evidence

- `git diff --check`：PASS
- `python3 scripts/validate-foundation.py`：PASS
- Ruff check / format：PASS
- mypy `src scripts`：PASS
- pytest：443 PASS / 1 known local environment-state failure. The local public schema contains PR #39 Draft
  tables `impact_analyses` and `event_fact_snapshots`; current-main allowlist correctly rejects them. No DB state
  was changed to mask the mismatch.
- package review：PASS for required files, links and freeze markers. It detected the ignored local `.env`; the file
  was not read, modified, tracked or packaged.
- review protocol self-containment：`docs/REVIEW_PROTOCOL.md` is Git-tracked and present in clean `git archive`;
  `AI_CONTEXT.md` does not depend on an untracked local file.

## 7. Implementation Review evidence (PR #43)

- R1 persistence ends at canonical `RawItem`; existing safe projection sidecars remain an in-memory handoff. PR #43
  does not create ContentItem, EvidenceItem, EventCandidate, Fact or AI records. Durable provider projection is R2;
  Event/Evidence/Fact completeness is R8.
- Finnhub numeric quote fields, EIA metric/value/unit and revision identity, SEC same-accession revision identity,
  and expanded Marketaux summaries are explicit R2/R3–R5 prerequisites. No zero, presence flag or placeholder is
  persisted as a factual value.
- Canonical RawItem idempotency is preserved. The current schema proves which run first persisted a canonical item;
  it does not persist every later run observation. Adding a run↔item observation association requires a reviewed
  additive schema and is a later prerequisite for observation/revision analytics, complete replay provenance and
  Pre-AI Data Readiness—not a PR #43 merge blocker. Implementations must not overwrite `RawItem.collection_run_id`
  or create duplicate canonical RawItems to fabricate lineage.
- Migration A remains expand-only. Phase 2 is an explicit all-or-nothing service with exact existing-target
  comparison, historical provenance preflight, unique candidate checks, exact count reconciliation and value-free
  AuditLog evidence.
- Rollback ownership is unique across every lifecycle until Migration B. Fake compatibility is allowlisted only for
  deterministic test/history migration and never activates a production Provider.
- Current v1 cursor support is limited to fake strict-incremental, Marketaux/Finnhub compound ordering, and EIA/SEC
  snapshot-watermark ordering. Same-period EIA revisions and same-accession SEC revisions cannot be proven from the
  R1 safe projection and remain R2/R4/R5 prerequisites; unsupported revision strategy fails closed.
- `legacy` remains the default authority. Shadow has a real read-only Beat audit and performs no enqueue/request/
  credential/write. Unified tasks require persisted reviewer-approved authority evidence before enqueue or network.
  No activation, cutover, replay or Migration B was executed.
- Notification reconciliation applies provider/content-kind eligibility before its bounded LIMIT. Candidates that
  pass that SQL policy gate but fail value-level safety validation receive a value-free, versioned scan AuditLog so
  later eligible content cannot be starved by a permanently repeated first page. No Finnhub quote or ordinary EIA
  observation is converted into a notification.
