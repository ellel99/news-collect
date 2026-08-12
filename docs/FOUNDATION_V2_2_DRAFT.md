# Foundation v2.2 Transition Draft

Status：Superseded by approved `docs/FOUNDATION_V2_2.md`

Historical review artifact：the approved text is frozen in `docs/FOUNDATION_V2_2.md`.

## Purpose

Record the minimum transition after Phase 1 core Information Collection & Push technical acceptance. This is
not a rewrite of the product Foundation and does not authorize implementation before Freeze Review.

## Proposed transition

1. Phase 1 Content First is complete for the approved four-Provider core path and remains supported.
2. The next engineering stage may become Event Intelligence / Event First.
3. Layer authority is explicit:
   - RawItem is the original collection trace/provenance layer.
   - EvidenceItem is the factual/provenance authority for Event Intelligence.
   - ContentItem is the existing content-safe display/projection layer. It may provide a safe deterministic
     clustering input, but it is not Event factual authority.
   - EventCandidate is an additive intelligence entity. It never deletes, overwrites, replaces, or weakens
     RawItem, EvidenceItem, or ContentItem.
4. Event grouping is deterministic, explainable, idempotent, reversible, provider-neutral, and fail-closed.
5. Real AI remains prohibited until a later independently reviewed SPEC. SPEC-0039 may only establish an
   IO-free ImpactAnalyzer contract and deterministic mock.
6. Trading actions, recommendations, portfolio advice, and automated execution remain prohibited.

## Impact analysis

- Schema：an approved SPEC-0039 implementation may add only EventCandidate and its Evidence association.
- Runtime：existing collection/evidence/ContentItem/scheduler/Telegram behavior must remain unchanged.
- Data ownership：EvidenceItem stays authoritative for Event facts and provenance. ContentItem remains a safe
  display/projection input only. EventCandidate never deletes or replaces RawItem, EvidenceItem, or ContentItem.
- Security：Event/AI boundaries may consume only safe internal projections, never secrets or raw provider payload.
- Operations：migration must be isolated, reversible, PostgreSQL-tested, and included in startup migration health.
- Deferred scope：X, backup/restore completion, management Bot, market validation runtime, real AI, and research
  recommendations remain separate work.

## Freeze Review decisions required

- Approve the Phase 1 core technical acceptance statement and deferred-capability list.
- Approve EventCandidate as the first additive Event Intelligence persistence entity.
- Approve deterministic pre-dedup and rule-based clustering boundaries.
- Approve the mock-only ImpactAnalyzer contract boundary.
- Confirm that v2.1 remains historical Phase 1 authority and that no recommendation semantics are introduced.
