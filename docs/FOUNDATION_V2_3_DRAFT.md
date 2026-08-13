# Foundation v2.3 Draft — Pre-AI Collection Readiness

Status：DRAFT — Freeze Review required; not effective

Proposed Date：2026-08-13

Current effective Foundation：v2.2-FROZEN

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

## 3. Boundaries that remain frozen

- Single-user/private deployment; no tenant, workspace, team or billing model.
- U.S. equities/ETF and Crypto remain the direct market scope; energy/macro/regulation remain explanatory inputs.
- Broad Scan and Controlled Push remain separate; implicit behavior cannot narrow collection.
- RawItem/Evidence provenance and content-safe boundaries remain authoritative and additive.
- No secret, credential, authorization header, secret-bearing URL or unlicensed full content in persistent
  config, task payload, logs, review artifacts or downstream Event/AI inputs.
- No access-control bypass, paywall bypass, unauthorized scraping or automatic provider fallback.
- No BUY/SELL/HOLD, position sizing, target price, portfolio advice, automated trading or broker integration.
- No new provider, X, streaming/webhook/event-bus infrastructure, semantic clustering or market-validation
  runtime without a separate SPEC and applicable review.
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
