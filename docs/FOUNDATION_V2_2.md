# Foundation v2.2 — Event Intelligence Transition

Status：FROZEN

Frozen Date：2026-08-13

Review Result：PASS

## Approved transition

1. Phase 1 core Information Collection & Push technical path is accepted for the approved four Providers.
2. Phase 1 Content First remains supported; the next engineering stage is Event Intelligence / Event First.
3. RawItem is the original collection trace/provenance layer.
4. EvidenceItem is the factual/provenance authority for Event Intelligence.
5. ContentItem is a content-safe display/projection layer and may be a safe deterministic clustering input;
   it is not Event factual authority.
6. EventCandidate is additive and never deletes, overwrites, replaces, or weakens existing provenance layers.
7. EventCandidate grouping must be deterministic, explainable, idempotent, reversible, provider-neutral,
   fail-closed, and constrained by stable immutable identity.
8. SPEC-0039 may implement only EventCandidate persistence/Evidence association, deterministic Level-1
   clustering, deterministic importance scoring, and a mock-only ImpactAnalyzer contract.

## Continuing prohibitions

- No real LLM/API, model credentials, embeddings, vector database, or semantic LLM clustering.
- No BUY/SELL/HOLD, target price, position sizing, portfolio advice, automated trading, or broker integration.
- No Market Validation runtime, new Provider, X, streaming/webhook/event-bus infrastructure, or scheduler rewrite.
- Existing Phase 1 Provider/Evidence/ContentItem/Scheduler/Telegram behavior must remain compatible.

## Traceability

- Approved Decision：D-025.
- Active implementation authority：SPEC-0039.
- SPEC-0022 is absorbed/superseded by SPEC-0039 and must not become a competing implementation.
