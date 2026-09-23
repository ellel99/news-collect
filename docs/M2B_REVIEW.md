# M2-B Rich Evidence Packet — Implementation Review Package

Review status: PENDING. Production authority: `legacy`. PR #39 remains untouched Draft.

## Review surface

- SPEC-0046 and typed contracts in `rich_evidence/`.
- Deterministic read-only builder with typed retention/content/time policy, bounded scan/result keyset API and
  final serialized UTF-8 size budget.
- Migration 0010 default-deny guards the linked association, Projection, Observation, canonical RawItem,
  Evidence, Content and Source retention. Only association/projection/content `updated_at` is allowlisted.
- Packet reads use a single repeatable-read/read-only snapshot. Evidence and Content use separate durable unique
  canonical-adoption markers written in the handoff transaction; historical target versions come from frozen
  observation/run lineage.
- Migration 0010 performs value-free fail-closed existing-state preflight. Handoff and mutation guards share
  `Source -> RawItem` advisory locking, followed by row locks and complete locked-state contract/provenance
  revalidation. Destructive integration tests require an explicit allowlisted PostgreSQL test database URL.
- Batch packet reads use bounded 100-row set-based prefetch. Query gates are 8 statements at scan sizes 1/50
  and 36 at the hard maximum 500, including the repeatable-read snapshot statement.
- Migration compatibility accepts only deterministically recognizable legacy opaque Finnhub quote/EIA retail
  Evidence identities; it never rewrites historical Evidence or relaxes new Evidence policy.
- PostgreSQL fixtures for six operation paths, revisions, numeric preservation, tamper rejection and direct SQL.

## Deliberate exclusions

No durable packet table, Provider/raw payload read, Notification, Event association, Fact, ImpactAnalysis,
Rich Evidence consumer, model/API call, production migration, authority activation, cutover, replay or M2-C/D.
