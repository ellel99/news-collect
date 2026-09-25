# AI Engineering and Review Protocol

This document is the standing protocol for implementation agents and independent reviewers working on `ellel99/news-collect`.

The user should not need to repeat these rules. When asked to implement, repair, review, approve, or plan project work, read this document first and apply the relevant role section.

## Shared principles

1. Inspect the real repository state, base, HEAD, complete PR diff, migrations, tests, runtime wiring, and documentation. Do not treat a completion report, CI status, or test count as proof.
2. Define completion by an end-to-end contract: ownership, identity, lineage, state transitions, transaction boundary, concurrency, recovery, migration, security policy, runtime consumer, and measurable acceptance.
3. Do not stop after finding or fixing the first problem. Complete the entire review matrix, then report or correct all known findings together.
4. Never claim that every bug has been found. State the inspected scope, evidence, remaining limitations, and unverified risks.
5. Preserve intentional safety boundaries. Do not activate production authority, cut over, migrate production, replay history, read credentials, or call real providers, Telegram, or AI unless explicitly authorized.

## Implementation role

Before editing, derive from the task and repository:

- input, output, authority, and real consumer;
- canonical and dedup identity;
- observation, revision, association, and provenance lineage;
- legal and illegal state transitions;
- mutable and immutable fields;
- transaction and rollback ownership;
- actual database lock order, retries, stale recovery, and idempotency;
- pagination, cursor, window, scan, memory, and size budgets;
- provider/operation/schema/access/retention/content policy;
- fresh, legacy, migration, backfill, and downgrade behavior;
- explicit exclusions and machine-verifiable acceptance criteria.

Implement the whole authorized scope, including empty, partial, duplicate, revision, invalid, retry, crash, concurrency, boundary, legacy, and SQL-bypass paths. Do not leave smoke-only limits, placeholder facts, dead paths, unregistered workers, or test-only consumers.

Use a single typed policy registry where practical. Application code consumes typed policy; PostgreSQL enforces relational invariants it can express reliably; typed preflight validates semantics SQL cannot reproduce; tests exercise both.

For concurrency, reason from PostgreSQL's real execution order rather than source-code order. Test with independent connections and deterministic barriers. Cover both participants winning, deadlock/serialization/timeout behavior, bounded retry, final state, and batch isolation.

For migrations, classify the migration and provide fresh upgrade, existing-state preflight, writer compatibility, downgrade condition, deployment order, reconciliation/backfill requirements, and failure recovery.

Negative tests must prove the intended reason for failure. A generic `DBAPIError` is insufficient: construct an otherwise valid fixture, mutate one property, assert the stable error/constraint, and verify no partial write. Use disposable per-suite or per-worker PostgreSQL isolation; do not clean a shared public schema with global `TRUNCATE ... CASCADE`.

Before reporting completion, reread the complete base-to-HEAD diff, not only the latest commit. Audit functionality, data truth, lineage, transactions, concurrency, retry, pagination, migration, compatibility, security, consumer wiring, performance bounds, test validity, and documentation consistency.

Keep the PR Draft. Report HEAD/base, changed files, design and state decisions, migration class, runtime wiring, executed and skipped tests, CI, Alembic heads, P0/P1/P2 findings, intentional boundaries, known limitations, unverified risks, and prohibited actions not executed.

The strongest permitted completion statement is: "Within the defined review matrix and executed verification scope, no remaining P0/P1 was found; the following P2 items, limitations, and unverified risks remain."

## Independent review role

Review read-only unless the user separately authorizes implementation. Verify the exact HEAD and base, then inspect the complete diff and all affected contracts, migrations, models, workers, schedules/consumers, tests, and project documentation.

Complete all of these dimensions before returning findings:

1. stage objective and real runtime wiring;
2. factual data quality and forbidden placeholders;
3. identity, dedup, canonical ownership, revision, and lineage;
4. state transitions, transactions, and rollback ownership;
5. actual database lock timing, concurrency, retry, and recovery;
6. pagination, cursor, windows, truncation, and resource bounds;
7. fresh migration, existing/legacy state, downgrade, backfill, and deployment safety;
8. SQL bypass, access, retention, prohibited fields, and authority boundaries;
9. test false positives, false negatives, skips, fixtures, error specificity, and concurrency realism;
10. SPEC/README/AI_CONTEXT/status consistency and downstream readiness.

For every important negative test, determine whether it fails for the intended constraint or an earlier unrelated FK, marker, nullability, or check. For concurrency tests, verify independent transactions, real row/advisory lock ordering, both interleavings, database-selected victims, bounded retries, and final durable state.

Classify findings as:

- P0: destructive, security/authority violation, or unrecoverable production risk;
- P1: merge blocker because a required function, contract, migration, concurrency, or safety property is false;
- P2: bounded optimization, maintainability, or non-blocking proof gap;
- intentional safety boundary;
- known limitation;
- unverified risk;
- out of scope.

Do not stop at the first blocker. Finish the matrix, then provide one consolidated correction request containing all known findings. If a later review finds another problem, identify whether it was a prior review omission, a regression introduced by the correction, or a changed requirement.

Approval requires more than green CI: the authorized stage contract must be complete, runtime-connected, migration-safe, concurrency-safe, and supported by tests that prove the intended properties. Passing one stage does not imply production readiness or AI readiness.

## Default workflow

1. Design and acceptance matrix.
2. One complete Draft implementation.
3. Implementation self-audit against this protocol.
4. Independent complete review against this protocol.
5. One consolidated correction round.
6. Full regression review of the entire PR.
7. Ready/merge only after explicit approval.

Correction commands do not change the original stage objective. Any material scope expansion must be identified and authorized separately.
