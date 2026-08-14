# R1 Unified Production Collection Control Plane — Docs Review Package

Status：DRAFT — Reviewer decision PENDING

Foundation：v2.3-FROZEN

R0：Completed / PASS

Active SPEC：SPEC-0041 Implementation — Docs Review only

Implementation：NOT AUTHORIZED / NOT STARTED

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
- rollback activation permits at most one active target per `(source_account_id,legacy_cursor_type)`; same-account
  multi-target begins only after Migration B and rollback-window closure.
- PR #39 Draft migrations are excluded; implementation revision derives from the then-current real main head.

## 3. Review checklist

- [ ] Final fields, nullability, enums, checks, indexes, FKs and lifecycle are implementable.
- [ ] Identity, config and secret constraints are unambiguous.
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
- [ ] the normative state matrix and config-revision in-flight state machine cover all terminal/retry/manual states.
- [ ] exact implementation files, five acceptance batches (four functional + Migration B) and test matrix are accepted.
- [ ] R2–R8, Provider expansion, Event/Evidence/Fact/AI and Market Validation remain out of scope.
- [ ] PR #39 remains Draft and untouched.

## 4. Non-actions in this PR

- No Python, Alembic revision, ORM/schema, runtime config or test behavior change.
- No Provider, Telegram or AI request; no credential/`.env` read.
- No live migration, target bootstrap, scheduler cutover or production activation.
- No R2 or later readiness work.

## 5. Reviewer result

Current result：**PENDING**.

Reviewer must explicitly record `PASS` or `REQUEST CHANGES`. A PASS does not start implementation.

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
