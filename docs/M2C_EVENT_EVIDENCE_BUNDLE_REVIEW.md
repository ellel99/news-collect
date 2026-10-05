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
4. Relation rules are deterministic and descriptive within one exact fact-identity group. They do not adjudicate
   truth or compare semantic meaning across Providers.
5. Reprocessing identical membership/packet material is idempotent. Packet revision or membership change appends
   a new bundle revision.
6. PostgreSQL checks immutable history, item association/Evidence/projection provenance and head ownership.
7. Reconciliation is bounded and authority-neutral, uses `FOR UPDATE SKIP LOCKED`, finite retry and stale recovery.
8. Existing-state preflight is read-only and value-free. Downgrade refuses nonempty M2-C state.
9. M2-D receives bundle version/digest/status, ordered items, packet/projection hashes, diversity and time range.
10. The service revalidates the worker claim token under lock in the revision transaction; recovered stale claims
    cannot create history or advance the head. PostgreSQL independently caps active membership and revisions at
    500 items and requires head pointers to name the latest current and latest READY canonical revisions.
11. An EventCandidate with no active membership has no current head. Reconciliation removes only that mutable
    pointer, preserves immutable revisions, and can reopen the blocked job after a reviewed association is added.
12. One revision reads all packets from one repeatable-read snapshot and the commit transaction rejects changed
    projection identity/hash/count. Exhausted transient dependencies reopen only after a persisted value-free
    dependency fingerprint changes; unchanged input remains bounded BLOCKED.

## Explicitly absent

No AI association or conflict decision, cheap/strong model, Fact/ImpactAnalysis, recommendation, production
migration/activation/cutover, historical replay, external request, raw response persistence or PR #39 change.

## Implementation self-audit evidence

- Complete `origin/main...HEAD` audit covered bundle identity, membership ownership, relation rules, deterministic
  digest, the 500-member service/database cap, append-only revision/head semantics, claim-token ABA protection,
  retry/stale recovery, authority-neutral Celery wiring, 0011 upgrade/downgrade, and the M2-D input boundary.
- Initial CI exposed duplicate enum creation. The migration now creates each enum exactly once and uses
  `create_type=False` for table columns; downgrade removes deferred triggers before their tables.
- Historical migration round-trip fixtures explicitly exclude/remove 0011 dependants rather than using CASCADE.
- PostgreSQL rejects a head rollback and enforces exact latest-current/latest-READY pointers. Losing the final
  active association removes only the mutable head and never deletes a revision.
- The controlled preflight is tested in PASS and value-free BLOCKED states. Missing not-yet-linked packet state and
  concurrent membership changes use bounded RETRY; invalid packet/provenance and exhaustion remain BLOCKED.
- GitHub quality CI executed the complete PostgreSQL/Redis suite: 873 passed. Local PostgreSQL tests were not run
  because no local PostgreSQL service was available; the 15 M2-C PostgreSQL tests executed in GitHub CI.
- No P0/P1 remained within the defined self-review matrix after these corrections. Independent Implementation
  Review remains required; this statement is not approval or production authorization.

Known limits: relation v1 is exact deterministic fact-identity/value comparison rather than semantic adjudication;
packet construction is bounded at 500 Evidence memberships and intentionally does not implement AI, Fact,
ImpactAnalysis, production migration, activation, backfill, or M2-D.

## Review result

PENDING — keep the PR Draft. Independent Implementation Review is required.
