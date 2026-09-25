# M2-C Event Evidence Bundle — Draft Implementation Review Package

## Review baseline

- Foundation: v2.3-FROZEN
- Active SPEC: SPEC-0047
- Parent: M2-B / Alembic 0010
- Migration: additive 0011; production execution is not authorized
- Production collection authority: `legacy`

## Review matrix

1. RichEvidencePacket is the only factual input; no provider payload or SDK crosses the boundary.
2. EventCandidate owns active membership; bundle revisions never alter clustering or Evidence.
3. Bundle/item history is append-only; the head owns current and latest READY canonical pointers.
4. Relation rules are deterministic and descriptive. They do not adjudicate truth.
5. Reprocessing identical membership/packet material is idempotent. Packet revision or membership change appends
   a new bundle revision.
6. PostgreSQL checks immutable history, item association/Evidence/projection provenance and head ownership.
7. Reconciliation is bounded and authority-neutral, uses `FOR UPDATE SKIP LOCKED`, finite retry and stale recovery.
8. Existing-state preflight is read-only and value-free. Downgrade refuses nonempty M2-C state.
9. M2-D receives bundle version/digest/status, ordered items, packet/projection hashes, diversity and time range.

## Explicitly absent

No AI association or conflict decision, cheap/strong model, Fact/ImpactAnalysis, recommendation, production
migration/activation/cutover, historical replay, external request, raw response persistence or PR #39 change.

## Review result

PENDING — keep the PR Draft. Independent Implementation Review is required.
