# Foundation v2.3 Draft — Pre-AI Collection Readiness

Status：DRAFT — Freeze Review required; not effective

Proposed Date：2026-08-13

Current effective Foundation：v2.2-FROZEN

Review package：`docs/FOUNDATION_V2_3_FREEZE_REVIEW.md`

## 1. Why a Foundation revision is required

Foundation v2.2-FROZEN prohibits a scheduler rewrite and limits implementation authority to SPEC-0039.
SPEC-0041 proposes a new `CollectionTarget` schema, target-owned runtime state, a unified production scheduler
and collection/delivery decoupling. Those changes cannot be implemented under v2.2 merely because they improve
reliability. This draft prepares the required Freeze Review; it does not approve itself.

PR #39/SPEC-0040 remains Draft and paused. No real AI implementation may proceed while the Pre-AI Collection
Readiness Program is incomplete.

## 2. Proposed authorization

If and only if this revision receives an explicit Freeze Review PASS, v2.3 would authorize bounded, reviewed
SPECs to:

1. repair and complete Phase 1 collection reliability before real AI;
2. add `CollectionTarget` and target-owned cursor/run/health/state schema;
3. replace the fake-only plus special-provider scheduling split with one target-driven, provider-neutral
   production collection control plane;
4. use typed/versioned provider operation configuration and explicit allowlisted adapter factories;
5. inject credentials only in worker runtime and keep them out of DB/task/config/logs;
6. decouple collection from Telegram and Event delivery so downstream credentials or failures never stop
   collection;
7. add a durable content-safe projection boundary and expand only separately reviewed provider operations;
8. complete provider-neutral Event/Evidence/Fact inputs before reconsidering deterministic+model AI routing.

Approval of this Foundation would authorize creation/review of bounded implementation SPECs, not automatic
implementation of every readiness step or provider operation.

### 2.1 Exact authorization boundary

The proposed authorization is limited to the following readiness domains:

| Domain | What v2.3 would permit to be specified/reviewed | What it does not authorize |
|---|---|---|
| Pre-AI collection reliability | reliability work required before real AI | arbitrary Phase 1 rewrite |
| CollectionTarget/state | target identity and target-owned schedule/cursor/lock/retry/run/health/dispatch state | credentials or provider payload in control data |
| Unified control plane | replacement of fake-only/special-provider split | new provider or arbitrary endpoint |
| Scheduler rewrite | bounded migration to target-driven production scheduling | implementation without an Active SPEC |
| Delivery decoupling | collection independent from Telegram/Event delivery | Telegram content/routing expansion |
| Durable safe projection | versioned, provider-neutral, provenance-preserving projection | raw payload propagation or unlicensed content |
| Provider/source operation readiness | R7 may prepare independent SPECs for in-scope Company IR, official RSS and government/macro/regulatory official endpoint families; other operation expansion requires its own review | creating, activating or requesting any source/endpoint from R0 itself; commercial Provider/X/arbitrary crawling |
| R8 completeness | Event/Evidence/Fact completeness after collection readiness | real AI, Market Validation or recommendation |

R1–R8 remain separately reviewed work. An R0 PASS changes the governance ceiling only; it does not activate a
SPEC, create a branch, authorize a migration, or permit a runtime request.

## 3. Boundaries that remain frozen

- Single-user/private deployment; no tenant, workspace, team or billing model.
- U.S. equities, U.S. ETFs, Crypto, and related cash positions remain the direct market/portfolio scope; macro,
  energy, regulation, bonds, FX and commodities remain explanatory inputs unless a later Foundation Revision
  changes that boundary. This inheritance does not authorize Portfolio/Holding/Investment Plan implementation.
- Broad Scan and Controlled Push remain separate; implicit behavior cannot narrow collection.
- RawItem/Evidence provenance and content-safe boundaries remain authoritative and additive.
- No secret, credential, authorization header, secret-bearing URL or unlicensed full content in persistent
  config, task payload, logs, review artifacts or downstream Event/AI inputs.
- No access-control bypass, paywall bypass, unauthorized scraping or automatic provider fallback.
- No BUY/SELL/HOLD, position sizing, target price, portfolio advice, automated trading or broker integration.
- R0 itself cannot create, activate or request any new Provider, Source, feed or endpoint. R7 may only prepare an
  independent SPEC for an in-scope Company IR, official RSS or government/macro/regulatory official endpoint
  family. Each requires official identity, access/license/robots/retention/attribution, typed operation/adapter,
  budget/cursor/revision/recovery, mock/integration, explicitly authorized bounded live, and production-activation
  reviews before use.
- Commercial news Providers, X, streaming/webhook/event-bus infrastructure, arbitrary web crawlers/endpoints,
  semantic clustering and Market Validation remain prohibited. A source outside existing Collection Scope,
  direct-market scope or safety boundaries requires another Foundation Revision.
- Existing Phase 1 and SPEC-0039 behavior remains compatible; migration and cutover must be reversible.

## 4. PR #39 freeze gate

- PR #39/SPEC-0040 must remain Draft and must not merge until the complete Pre-AI Collection Readiness Program
  has passed its defined acceptance gates.
- After readiness completion, PR #39 must be re-audited and rebased on the then-current `main`.
- No promise is made that its current AI contract, Fact digest, snapshot schema or routing design will survive
  unchanged; the re-review must compare them with the final durable projection and Event/Evidence/Fact contracts.
- Its Alembic revisions must be reconciled to the final serial merge order. Double heads, copied revision IDs,
  invented bridges and rewriting already-published migration history are prohibited.

## 5. Freeze Review checklist

- [ ] User/Reviewer accepts collection reliability work before real AI.
- [ ] `CollectionTarget` and target-owned state/schema boundary is approved.
- [ ] Unified scheduler/control-plane rewrite is explicitly approved despite v2.2 prohibition.
- [ ] Collection and Telegram/Event delivery decoupling is approved.
- [ ] Pre-AI Readiness steps, provider expansion gates and stopping conditions are accepted.
- [ ] Existing single-user/Broad Scan/security/content/trading boundaries are confirmed unchanged.
- [ ] PR #39 freeze/re-audit/rebase and migration sequencing rules are accepted.
- [ ] Foundation, Decisions, Roadmap, SPEC_INDEX and Active SPEC are updated only after explicit PASS.

## 6. Current review state

Result：PENDING — no Freeze Review decision recorded.

Until PASS, Foundation v2.2-FROZEN remains fully effective and SPEC-0041 cannot implement schema, scheduler,
factory, migration, projection or provider expansion.

## 7. Proposed v2.2 → v2.3 normative delta

If Freeze Review later returns PASS, only the following Foundation-level delta may be applied:

1. Replace the absolute v2.2 prohibition on `scheduler rewrite` with a narrow exception for the reviewed,
   target-driven production collection control plane and its reversible migration.
2. Replace “Active implementation authority: SPEC-0039” with “SPEC-0039 remains completed; R1–R8 may each be
   activated only through an independent approved SPEC after R0.”
3. Add `CollectionTarget` and target-owned state as permitted control-plane schema, without prescribing an
   implementation before R1 review.
4. Add durable safe projection as a permitted pre-AI foundation layer, subject to provenance, content/license/
   retention and secret boundaries.
5. Permit R7 to prepare independent SPECs for in-scope official-source endpoint families and permit operation
   expansion only after all source/contract/runtime/production gates. Do not create, activate or request any
   Provider, Source, feed, route, endpoint or target in Foundation. Commercial news Provider, X, arbitrary crawler
   and out-of-scope source work remain prohibited or require a later Foundation Revision as applicable.
6. Require collection to remain operational independently from Telegram/Event delivery.
7. Require R8 Event/Evidence/Fact completeness before any real AI routing review.
8. Continue every v2.2 single-user, market-scope (including related cash positions), Broad Scan, Controlled Push, credential, provenance,
   content/license, no-unauthorized-access and no-trading boundary unchanged.
9. Continue to prohibit real AI, Market Validation, Recommendation and Portfolio implementation. PR #39 remains
   Draft until R0–R8 complete and must then be re-audited/rebased.

No other v2.2 clause is proposed for deletion or relaxation.
