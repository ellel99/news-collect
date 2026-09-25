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
- Migration 0010 performs value-free fail-closed existing-state preflight. Handoff uses fixed row locks and a
  bounded timeout; mutation triggers take no post-row-lock advisory locks. Concurrency failures are value-free,
  bounded per-item retries. Tests run in a random token-bound disposable PostgreSQL database per pytest process.
- Batch packet reads use bounded 100-row set-based prefetch. Query gates are 8 statements at scan sizes 1/50
  and 36 at the hard maximum 500, including the repeatable-read snapshot statement.
- Migration compatibility accepts only exact deterministic legacy opaque identities from the historical mapper
  matrix: Marketaux news, Finnhub quote, EIA retail and SEC submissions. Finnhub company news, EIA RTO and new
  operations are rejected. Adoption retains an unreconstructable historical provider hash without claiming full
  hash equality; it never rewrites historical Evidence or relaxes new Evidence policy. Marketaux uses one
  typed ASCII identity contract across adapter, projection validator, Python hash and SQL; UUIDs are accepted,
  outer whitespace is normalized, and quote/Unicode/internal-whitespace ambiguity fails closed.
- Applying 0010 requires the controlled `m2b_controlled_upgrade.py` maintenance-lock entry, which verifies 0009,
  writers-stopped acknowledgement, typed preflight and unchanged state before upgrading. Bare production Alembic
  upgrade is prohibited; migration SQL remains the complementary relational audit. DBA-installed `pgcrypto` is
  a read-only checked prerequisite; 0010 does not install extensions and fails value-free before schema changes
  when the prerequisite is absent or `digest(bytea,text)` cannot resolve through the effective `search_path`.
- PostgreSQL fixtures for six operation paths, revisions, numeric preservation, tamper rejection and direct SQL.

## Deliberate exclusions

No durable packet table, Provider/raw payload read, Notification, Event association, Fact, ImpactAnalysis,
Rich Evidence consumer, model/API call, production migration, authority activation, cutover, replay or M2-C/D.
