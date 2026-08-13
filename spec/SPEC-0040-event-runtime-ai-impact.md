# SPEC-0040 — Event Processing Runtime + Real AI Impact Analysis

Status：Active — Implementation Review

Phase：Event Intelligence — bounded runtime and impact analysis

Foundation：v2.2-FROZEN

Depends on：SPEC-0039（Completed）

## Objective

Implement one bounded, provider-neutral path:

`validated EvidenceItem → EventProcessingRuntime → EventCandidate → FactSnapshot → ImpactAnalyzer → impact_analyses`

This SPEC combines its reviewed contract and implementation in one bounded PR under explicit user authorization.
It does not authorize a real AI request; the manual smoke remains dry-run until the user supplies a process
environment credential and separately authorizes exactly one `--execute` request.

## Runtime boundary

- Provider adapters never call Event or AI code.
- `event_intelligence.process_evidence` accepts an already-persisted Evidence UUID and uses only the database.
- Candidate processing is idempotent and isolated in a savepoint; failures do not delete or roll back previously
  committed RawItem/EvidenceItem or alter Phase 1 scheduler/Telegram delivery.
- Duplicate tasks return `NO_CHANGE`; safe states are `PASS`, `NO_CHANGE`, `RETRY`, `FAILED`, `BLOCKED`.
- AI retry state is separate from Provider collection and bounded by the caller; no infinite retry or event bus.

## Fact Layer

`FactSnapshot` is the only AI input. It includes candidate identity/version, a content-safe `what_happened`,
entities/companies/assets/sectors/topics, event timestamps, active Evidence/source counts, official-source presence,
source types, opaque Evidence refs, corroboration, contradictions, uncertainty, freshness, provenance summary, and
a deterministic SHA-256 snapshot hash.

It reads active Evidence associations plus approved ContentItem title projections. It never reads provider SDK
objects, raw provider responses, credentials, unrestricted body, local captures, or `.env`. No Evidence yields a
fail-closed result. Conflicting safe projections are represented as contradiction/uncertainty rather than hidden.

## ImpactAnalyzer boundary

The existing provider/model-neutral `ImpactAnalyzer` protocol and deterministic mock remain authoritative.
SPEC-0040 adds a replaceable structured transport boundary and one explicitly bounded OpenAI Responses API
implementation; this is not a permanent Foundation provider selection. The implementation follows official
Structured Outputs guidance and validates the complete domain contract after parsing.

Required output:

- affected companies/assets/sectors and impact channels;
- direction: positive, negative, mixed, uncertain;
- horizon: immediate, short_term, medium_term, long_term;
- confidence 0..1, rationale, uncertainty, market-validation requirement, analysis version.

Missing/extra fields, invalid enums, bad confidence, malformed output, or BUY/SELL/HOLD/target-price/position-size/
rebalance/allocate/trade-now language fail closed. Provider/model SDK types never enter the domain contract.

## Persistence

Revision `0006` creates `impact_analyses` only. Each row records EventCandidate FK, monotonically increasing
analysis version, fact version/hash, analyzer provider/model/contract identity, validated structured output,
status, safe errors, optional superseded analysis FK, and creation time. Old versions are retained.

Idempotency key:

`event_candidate_id + fact_snapshot_hash + analyzer_provider + analyzer_model + analyzer_contract_version`

The same snapshot/config returns the existing analysis. A changed Fact or analyzer identity creates a new version
and links to the previous analysis. Retry/failed attempts are safe rows that can become valid for the same key;
raw model responses and prompts are never stored.

## Bounded smoke

`scripts/event_impact_smoke.py` defaults to inert dry-run: no credential read, network, or DB write. `--doctor`
checks exactly one eligible candidate and Fact construction without reading AI credentials. Manual `--execute`
requires `OPENAI_API_KEY` and `OPENAI_IMPACT_MODEL` from the process environment, chooses one candidate, permits
one AI request, performs no automatic retry, stores only validated structured analysis, and outputs a safe summary.
It never reads `.env`, saves response/prompt/content, or prints credentials.

## Tests and acceptance

- Event runtime new/repeated Evidence, failure isolation and duplicate-task behavior.
- Fact official/news aggregation, provenance, contradictions, uncertainty, insufficient Evidence, no raw payload.
- First/idempotent/changed-Fact versions, prior-version retention, analyzer identity, invalid rejection.
- Analyzer enum/horizon matrix, confidence/missing fields/trading language, mocked success/timeout/429/5xx/invalid.
- Smoke dry-run inert and execute-missing-credential fail closed; tests never make a real AI request.
- Revision 0006 PostgreSQL upgrade/downgrade/re-upgrade and Alembic check.
- All EventCandidate and Phase 1 Provider/scheduler/Telegram regressions remain PASS.

## Non-goals

No Market Validation runtime, price reaction, recommendation, portfolio/position/target price, automated trading,
broker integration, X/new Provider, embeddings/vector DB, semantic clustering, Event ML merge, Telegram AI push,
multi-user, SPEC-0041, or Foundation trading-boundary change.
