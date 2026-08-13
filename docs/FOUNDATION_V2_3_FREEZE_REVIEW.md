# Foundation v2.3 Freeze Review Package — R0

Status：DRAFT — Review decision required

Current effective Foundation：v2.2-FROZEN

Candidate Foundation：v2.3 Draft — Pre-AI Collection Readiness

Review result：PENDING（Reviewer must explicitly choose PASS or REJECT）

## 1. Review scope

This package reviews only the Foundation ceiling needed for the Pre-AI Collection Readiness Program. It does
not activate R1, authorize implementation, modify the database or approve any external request. The candidate
normative text is `docs/FOUNDATION_V2_3_DRAFT.md`.

## 2. v2.2 → v2.3 reviewable diff

| v2.2 effective rule | v2.3 proposed change | Reason | Preserved guardrail |
|---|---|---|---|
| Event Intelligence follows Phase 1 core acceptance | pause real AI and complete Pre-AI collection readiness first | current bounded runtime is not a multi-target production control plane | EventCandidate/Evidence remain additive and compatible |
| no scheduler rewrite | narrow exception for a reviewed target-driven scheduler/control-plane rewrite | generic scheduler is fake-only and real scheduler is provider-level/single-target | independent SPEC, reversible migration, no double collection |
| implementation authority limited to SPEC-0039 | SPEC-0039 stays completed; R1–R8 may be separately activated after R0 | readiness work otherwise has no governance authority | R0 PASS does not activate any step |
| no new collection state entity under v2.2 transition | permit CollectionTarget and target-owned state design/migration | cadence/cursor/lock/retry/run/health require stable target ownership | no credentials/raw payload; schema requires R1 Review |
| existing Scheduler/Telegram behavior remains compatible | permit collection/delivery decoupling while preserving outcomes | Telegram/Event failure must not stop collection | notification remains persistent/idempotent; content unchanged |
| ContentItem is content-safe projection | permit a durable, versioned safe projection layer | downstream facts cannot depend on ephemeral sidecars/raw payload | provenance, retention, license and redaction remain mandatory |
| no new Provider | unchanged; permit only independently reviewed operation expansion | current provider coverage needs depth, not implicit provider activation | Source Catalog/contract/SPEC/live gates remain |
| deterministic Event foundation only | permit R8 Event/Evidence/Fact completeness work | AI must consume complete, bounded factual inputs | no real AI/model call or semantic clustering |

## 3. Proposed authorized domains

An explicit PASS would permit preparation and independent review of bounded work in exactly these domains:

1. Pre-AI collection reliability.
2. `CollectionTarget` and target-owned state.
3. Unified production collection control plane.
4. Scheduler/control-plane rewrite.
5. Collection and Telegram/Event delivery decoupling.
6. Durable safe projection.
7. Provider operation expansion only through an independent SPEC and contract/license review.
8. R8 Event/Evidence/Fact completeness.

It would not authorize implementation by itself.

## 4. Frozen boundaries carried forward unchanged

- Single user/private system; no tenant, workspace, team, billing or public redistribution product.
- Broad Scan and Controlled Push remain distinct; implicit behavior cannot narrow coverage.
- Direct market scope remains U.S. equities/ETF and Crypto; macro/energy/regulation remain explanatory inputs.
- Credentials remain worker-runtime-only and absent from DB, config payloads, tasks, logs and review artifacts.
- Content access, license, retention, attribution and redistribution constraints remain source/operation specific.
- RawItem/Evidence provenance remains traceable and cannot be deleted or weakened by projection/Event layers.
- No access-control/paywall/captcha bypass, unauthorized scraping, arbitrary endpoint or provider fallback.
- No automatic trading, BUY/SELL/HOLD, target price, position sizing, portfolio advice or broker integration.

## 5. Still prohibited after an R0 PASS

- Real AI/model calls and PR #39 merge.
- Market Validation runtime.
- Research Recommendation.
- Portfolio/Holding/Investment Plan implementation.
- New Provider, X, streaming/webhook/event-bus infrastructure.
- R1–R8 implementation without its own SPEC, Review PASS and explicit authorization.
- Any credential read, Provider/Telegram/AI request or live migration based only on this review.

PR #39 must remain Draft through R0–R8. Afterwards it must be re-audited and rebased on current main; its AI
contract, Fact digest, snapshot or routing design is not pre-approved. Alembic must follow final serial merge order
without double heads, copied revisions or rewriting published history.

## 6. R1–R8 independent review gates

| Step | Subject | Required independent decision after R0 |
|---|---|---|
| R1 | Unified Production Collection Control Plane | schema/runtime SPEC + migration/cutover review |
| R2 | Durable Safe Projection | data/retention/provenance SPEC |
| R3 | Marketaux operation completeness | operation/plan/license/runtime SPEC |
| R4 | EIA route/series catalog | dataset/facet/revision SPEC |
| R5 | SEC multi-company/history/XBRL | official-data/fact/revision SPEC |
| R6 | Finnhub multi-symbol observations | typed observation SPEC; Market Validation excluded |
| R7 | Company IR/official RSS/macro/regulatory | one reviewed endpoint family at a time |
| R8 | Event/Evidence/Fact completeness | factual-input contract/schema SPEC |

R0 PASS would not start R1. The repository may remain with Active SPEC=None until the user separately activates
an R1 SPEC.

## 7. Consistency audit

| Document | Required R0 state |
|---|---|
| `docs/FOUNDATION.md`, `FOUNDATION_FROZEN.md`, `docs/FOUNDATION_V2_2.md` | v2.2-FROZEN remains effective; unchanged |
| `docs/FOUNDATION_V2_3_DRAFT.md` | Draft/PENDING; candidate authorization only |
| `docs/DECISIONS.md` | D-026/D-027 Proposed/under review; not Approved |
| `docs/ROADMAP.md` | R0 current governance review; R1–R8 not started |
| `AI_CONTEXT.md`, `README.md` | Active SPEC=None; v2.3 pending; no implementation authority |
| `spec/SPEC_INDEX.md` | SPEC-0041 Docs Review completed; R0 is not an implementation SPEC |
| PR #39/SPEC-0040 | Draft/frozen; no modification, merge or rebase |

## 8. Reviewer decision form

Reviewer must record exactly one result in a later review-fix/closeout commit:

- `PASS`：approve only the §3 domains and all §4–§6 guardrails; then update effective Foundation documents in
  a reviewed closeout. Do not auto-start R1.
- `REJECT`：v2.2-FROZEN remains effective; SPEC-0041 implementation and R1–R9 remain blocked.

Current decision：**PENDING**.

## 9. Evidence and non-actions

- Based on merged PR #40/SPEC-0041 Docs Review and existing repository evidence.
- `git diff --check`, Foundation validation, Ruff, format and mypy：PASS.
- Regression：443 PASS / 1 local environment-state failure. The local public schema still contains PR #39
  Draft tables `impact_analyses` and `event_fact_snapshots`, which current-main allowlist correctly rejects;
  no database state was changed to hide the mismatch.
- Package review required files, Markdown links and freeze markers：PASS. It detected the ignored local `.env`;
  the file was not read, modified, tracked or packaged.
- No Python, migration, ORM/schema, runtime config or test logic change.
- No Provider, AI or Telegram request.
- No credential or `.env` read.
- No live data migration.
- PR #39 was not modified, merged or rebased.
