# SPEC-0041 Implementation — Unified Production Collection Control Plane

状态：Active — Docs Review only；implementation not authorized

Readiness step：R1

Foundation：v2.3-FROZEN（R0 Completed / PASS）

创建日期：2026-08-13

## 1. 审核目标与授权边界

本文件把已通过的 SPEC-0041 架构收敛为可直接实施的 R1 合同。本轮只审核文档，不修改 Python、
Alembic、ORM/schema、runtime config 或测试逻辑，不读取 credential/`.env`，不执行 migration 或任何
Provider、Telegram、AI 请求。Docs Review PASS 仍不等于 implementation authorization。

PR #39/SPEC-0040 保持 Draft、不得修改/合并/rebase；其未合并 migrations 不属于 `main`。R1 实施必须
从实施授权时真实 `main` 和真实 Alembic head 开始。

## 2. 最新 main 实现审计

基线：`main@9c68dd6effe67d6f798fb080fdbffa6f80b77532`，Alembic 单 head `0005`。

- `Source` 当前拥有 provider、授权、retention、全局 schedule/汇总 health。
- `SourceAccount.collection_options` 是无版本 JSON；`CollectionCursor` 以 account+cursor_type 唯一。
- `CollectionRun`、`RawItem` 没有 stable target identity；RawItem 只通过 run/source/account 追溯。
- generic dispatcher 仅查询 `access_method="fake"`，按 Source cadence 调度，并把完整 target/config 放入
  Celery payload。
- worker 信任序列化 target 的大部分字段，再从 DB 验证 Source/Account；真实 Provider factory/credential
  不在该 task path。
- 四 Provider 的真实 cadence、retry 和 Telegram delivery 由 `scheduler/multi_provider_runtime.py` 的
  provider-level orchestration 处理，不能表达同 provider 多 target。
- `TargetLock` 已使用 owner token 和 compare-and-renew/release；dispatch marker 已使用 Redis SET NX EX。
- `CollectionRunner` 已有 batch persistence→checkpoint、retry、timeout、lock renewal 和 stale run recovery。
- `notifications` 已提供 durable SENT dedup、FAILED retry/stale SENDING recovery；`outbox_messages` 已存在，
  但当前 collection control plane 不依赖它写 Telegram。

这些事实是迁移输入，不是需要重写的 Phase 1 pipeline。

## 3. 最终职责

| 实体 | 最终职责 | 明确不承担 |
|---|---|---|
| `Source` | provider/source identity、`access_method`、authorization、license/retention 上限、全局 kill switch | target cadence、cursor、retry、run/health |
| `SourceAccount` | 可选外部账号/feed/组织 identity 与 verification；一个 account 可关联多个 target | production operation config、schedule、cursor |
| `CollectionTarget` | 一个稳定、独立授权和运行的 typed provider operation；拥有 cadence/state/budget identity | credential、任意 URL、raw payload、delivery state |
| operation config registry | `(provider_key, operation_key, config_version)` 的静态 typed decoder/validator | dynamic class、fallback、secret resolution |
| worker credential resolver | execute worker 内按 provider 的固定 env name 解析 credential | DB/task/config/dispatcher credential access |
| `CollectionRun` | target-bound attempt/run audit | delivery outcome |
| `RawItem` | 通过 immutable `collection_run_id → target_id` 保留 target provenance | 冗余、可漂移的 target copy |
| Notification/Outbox | downstream durable delivery/idempotency | 决定 collection 是否运行/成功 |

## 4. 最终 `collection_targets` schema

### 4.1 Enums

- `collection_target_status`: `draft`, `active`, `paused`, `blocked`, `retired`
- `collection_target_health_status`: `unknown`, `healthy`, `degraded`, `blocked`
- `collection_cursor_strategy`: `strict_incremental`, `snapshot_watermark`, `page_token`, `date_window`,
  `compound`, `revision`
- `collection_mode`: `incremental`, `snapshot`
- `collection_backfill_policy`: `disabled`, `manual_bounded`
- `collection_revision_policy`: `ignore`, `safe_replace`, `reconcile`
- `collection_run_mode`: `normal`, `backfill`

不得用自由字符串替代这些状态。

### 4.2 Columns

| column | PostgreSQL type | nullable/default | contract |
|---|---|---|---|
| `id` | UUID PK | no / `gen_random_uuid()` | immutable internal identity |
| `target_key` | varchar(160) | no | immutable global audit key |
| `source_id` | UUID FK→sources RESTRICT | no | provider/auth/license authority |
| `source_account_id` | UUID | yes | composite FK with source；null only for source-level operation |
| `operation_key` | varchar(100) | no | static registry key；not URL/class path |
| `operation_config_version` | smallint | no | `>0` |
| `provider_contract_version` | smallint | no | `>0`；must match adapter |
| `operation_config` | JSONB | no / `{}` | JSON object；typed non-secret allowlist |
| `status` | `collection_target_status` | no / `draft` | sole lifecycle gate；no redundant `enabled` |
| `cadence_seconds` | integer | no | `1..86400` |
| `batch_limit` | integer | no | operation unit；positive and registry ceiling |
| `max_requests_per_run` | smallint | no / `1` | `1..20` |
| `max_pages_per_run` | smallint | no / `1` | `1..20` and `<=max_requests_per_run` |
| `max_response_bytes` | integer | no | `1024..10_000_000` |
| `request_timeout_seconds` | integer | no | `1..60` |
| `max_runtime_seconds` | integer | no | `>=request_timeout_seconds`, `<=900` |
| `cursor_strategy` | enum | no | registered operation-compatible strategy |
| `cursor_version` | smallint | no / `1` | `>0` |
| `collection_mode` | enum | no | incremental or snapshot |
| `backfill_policy` | enum | no / `disabled` | normal runs cannot become backfill |
| `revision_policy` | enum | no / `ignore` | operation-specific |
| `rate_limit_group` | varchar(160) | no | static opaque group；no credential/account value |
| `priority` | smallint | no / `100` | `0..1000`; lower first |
| `next_due_at` | timestamptz | no | normal cadence gate |
| `next_retry_at` | timestamptz | yes | retry gate, independent of cadence |
| `last_attempt_at` | timestamptz | yes | target health audit |
| `last_success_at` | timestamptz | yes | target success audit |
| `consecutive_failures` | integer | no / `0` | `>=0` |
| `health_status` | health enum | no / `unknown` | target health, not Source authority |
| `last_error_code` | varchar(100) | yes | safe code only |
| `retired_at` | timestamptz | yes | required iff status=retired |
| `created_at`,`updated_at` | timestamptz | no / current timestamp | audit |

### 4.3 Final identity and constraints

`target_key` is **globally unique**: `UNIQUE(target_key)`. The rejected alternative
`UNIQUE(source_id,target_key)` is not retained. Format is `^[a-z0-9][a-z0-9._-]{0,159}$`; it is an opaque,
human-auditable slug, never a query/config hash or secret. A DB trigger rejects any update to `target_key`.

Additional constraints/indexes:

- add `UNIQUE(source_accounts.id, source_accounts.source_id)` and composite FK
  `(source_account_id,source_id) → source_accounts(id,source_id) ON DELETE RESTRICT`;
- partial due index `(next_due_at,priority,id) WHERE status='active'`;
- partial retry index `(next_retry_at,priority,id) WHERE status='active' AND next_retry_at IS NOT NULL`;
- indexes `(source_id,status)`, `(source_account_id,status)`, `(rate_limit_group,status)`;
- config must be JSON object; recursive service validation rejects secret keys/values, URLs, class/module/import
  fields, nested credentials and secret-bearing strings. DB text checks provide defense-in-depth for
  `api_key`, `api_token`, `token`, `authorization`, `password`, `secret`, `http://`, `https://`;
- only `status=active` is dispatchable. `retired` is terminal; rows are not physically deleted. `blocked→active`
  requires explicit reviewed repair. Config change creates an audit entry, increments config version as required,
  resets health to unknown and cannot mutate `target_key`.

## 5. Target-owned cursor, run and provenance

### 5.1 Cursor

`collection_cursors` is migrated from account-owned to target-owned:

- add `target_id UUID NOT NULL FK collection_targets(id) RESTRICT` after deterministic backfill;
- add `cursor_version smallint NOT NULL`, `run_mode collection_run_mode NOT NULL DEFAULT normal`,
  `continuation JSONB NULL`, `watermark_at timestamptz NULL`;
- final uniqueness: `UNIQUE(target_id,cursor_type,cursor_version,run_mode)`;
- `source_account_id` remains temporarily as read-only historical compatibility in R1, but no scheduler/worker
  may use it as cursor authority. Its later removal is a separate cleanup review.

Cursor envelope is typed/versioned and contains only operation position, stable tie-breaker, continuation and
watermark. It contains no secret, raw response or URL.

### 5.2 Run and RawItem

- add `collection_runs.target_id UUID NOT NULL FK collection_targets(id) RESTRICT` and
  `run_mode collection_run_mode NOT NULL DEFAULT normal`;
- add index `(target_id,started_at)` and partial unique index allowing at most one `RUNNING` row per target/run_mode;
- retain source/account columns for audited compatibility; DB trigger/check validates run source/account equals
  target source/account;
- **do not add `raw_items.target_id`**. Final target provenance is the single authoritative chain
  `RawItem.collection_run_id → CollectionRun.target_id → CollectionTarget → Source/SourceAccount`, avoiding a
  redundant target value that can drift;
- CollectionRun is immutable with respect to target/source/account after insert. Existing RawItem FK remains.

## 6. Typed operation registry and initial schemas

Registry key is exactly `(Source.access_method, CollectionTarget.operation_key,
operation_config_version, provider_contract_version)`. It is statically assembled in worker startup. Unknown key,
version, malformed config or provider mismatch blocks the target without fallback.

Initial schemas only describe already implemented bounded operations:

| provider / operation | v1 config (all other fields forbidden) | mode/cursor | hard batch ceiling |
|---|---|---|---|
| `marketaux/news_all` | `query: str[1..200]`; optional `language: ^[a-z]{2}$`; optional `symbols: list[str[1..20]],1..10` | incremental / compound `(published_at,uuid)` | 3 |
| `finnhub/quote` | `symbol: ^[A-Z0-9.:-]{1,20}$` | incremental / compound `(timestamp,symbol)` | exactly 1 |
| `eia/electricity_retail_sales` | only `dataset='electricity'`; frequency monthly and data field price are fixed by registered operation, not target config | snapshot / compound `(period,state,sector)` | 5 |
| `sec_edgar/submissions_recent` | `ticker: ^[A-Z0-9.-]{1,12}$`; `cik: ^[0-9]{1,10}$` normalized to ten digits | snapshot+revision / compound `(filing_date,accession)` | 10 |

`timeout_seconds` is removed from legacy Marketaux config and mapped to the target budget column. These historical
values are not production defaults or operation expansion. Marketaux new filters/pages, EIA new
routes/facets/frequencies, SEC history/company facts/XBRL and Finnhub new observation types belong to R3–R6.

Credential resolver names are fixed in code: `MARKETAUX_API_TOKEN`, `FINNHUB_API_KEY`, `EIA_API_KEY`, and the
runtime-composed SEC User-Agent from `SEC_USER_AGENT`+`SEC_CONTACT_EMAIL`. Dispatcher never reads them. Worker
reads only after reloading an active authorized target. No credential reference/value is stored in target/config,
task, Redis, run, log or safe error.

## 7. Request budgets and rate-limit groups

Effective limits are the minimum of registry hard ceiling, verified plan ceiling, target value and worker emergency
ceiling. Each run enforces requests, pages, batch items, response bytes, request timeout and wall-clock runtime.
Exceeded response bytes abort before parsing/persistence with safe error. No infinite pagination/retry.

Initial `rate_limit_group` values are static provider groups (`marketaux:default`, `finnhub:default`, `eia:default`,
`sec-edgar:public`). Future account-specific groups use an opaque internal account UUID, never credential/contact.
Redis token/cooldown keys are group-scoped. 429 honors bounded Retry-After; group cooldown does not block targets
outside the group.

## 8. Pagination, continuation, watermark, backfill and revision

- strict cursor advances only to `candidate>current`; equal is operation-defined duplicate/no-new, backward fails;
- snapshot cursor: equal=`NO_NEW_ITEMS`, newer advances, older fails;
- compound order is lexicographic stable tuple; missing tie-breaker fails closed;
- each page is persisted before continuation checkpoint; hitting budget stores continuation and returns partial,
  never false complete;
- final watermark advances only after all pages for the bounded window complete;
- normal and `manual_bounded` backfill use separate `run_mode` cursor rows; scheduler creates only normal runs;
- backfill requires a future explicit command/review and bounded start/end; no generic historical backfill in R1;
- revision operations retain official identity+revision marker; stale revision never overwrites newer factual state.

## 9. Scheduler, dispatch, worker and recovery contract

1. Scheduler keyset-pages active targets ordered by effective eligible time, priority, id.
2. It validates Source kill switch/authorization and Account enabled/identity before claim.
3. Due time is `next_retry_at` when set, else `next_due_at`; permanent config errors do not fast-loop.
4. Dispatch identity is `target_id + scheduled_slot + run_mode + operation_config_version`; Redis
   `SET NX EX` TTL covers slot+deadline+retry window. Celery task id derives deterministically.
5. Task payload is only `target_id`, scheduled slot, run mode and dispatch id—never config or credential.
6. Worker reloads target/source/account/config, verifies the dispatch identity/version, resolves an allowlisted
   factory, then credential. Paused/changed/unauthorized target fails closed before network.
7. Worker acquires Redis owner-token target lock; renew/release compare owner token. Lost lock prevents checkpoint.
8. Each successful batch atomically writes RawItem/run counters/cursor. Cursor never advances on persistence error.
9. Retryable failure sets target `next_retry_at` using existing bounded RetryPolicy/Retry-After and preserves the
   same CollectionRun attempt lineage. Success clears retry and advances normal cadence from completion.
10. stale recovery requires expired/missing owner lock and no live retry marker; it marks run failed safely, never
    advances cursor, and makes target eligible according to classified recovery policy.

One target failure/lock/retry cannot affect another target. Source success/failure fields become display-only
aggregates and never gate target scheduling.

## 10. Delivery decoupling and Notification/Outbox decision

R1 **reuses existing `notifications` and `outbox_messages` without schema extension**. Collection worker ends after
RawItem/downstream pipeline persistence and emits no Telegram call. A separate delivery task continues to claim
Notification rows and use existing dedup/retry/stale-SENDING semantics. Telegram/Event credentials or failures do
not change target cadence, run status, cursor or health.

R1 does not require a new delivery outbox migration: existing Notification is the durable Telegram delivery state;
existing Outbox remains available for transactional internal messages but is not made a second competing Telegram
state machine. Any future Outbox payload/schema expansion requires its own SPEC. Cutover tests must prove pending
notifications survive and are delivered independently.

## 11. Legacy migration, shadow, cutover and rollback

Migration must be additive and based on the implementation branch's real head (currently `0005`; never PR #39's
Draft `0006/0007`). One linear revision creates target enums/table, target-owned cursor/run fields and constraints.

1. Create schema nullable where backfill requires it; keep legacy scheduler authoritative.
2. Generate one target per existing source/account operation with deterministic non-secret target_key. Legacy
   AAPL/technology/electricity/CIK values become `draft` or `paused`, never automatically active.
3. Source-level fake rows get a source target. Ambiguous/invalid rows become `blocked` and produce value-free audit.
4. Map every historical run to the deterministic historical target. Map each cursor only when account+cursor codec
   has exactly one compatible target; ambiguity blocks migration before NOT NULL/final uniqueness.
5. Apply final NOT NULL/FK/index/trigger constraints only after reconciliation counts match.
6. Shadow mode compares old/new due decisions, target resolution and cursor position without network, enqueue or
   duplicate write. No dual authoritative scheduler.
7. Reviewer approves per-target activation. Stop legacy multi-provider Beat, drain in-flight runs, then enable the
   unified dispatcher as the **single authoritative scheduler**.
8. Rollback stops unified dispatch first, drains workers, restores legacy Beat, and preserves all target/run/cursor
   audit. No `down -v`, volume deletion, Alembic stamp fabrication or published-history rewrite.

Downgrade is allowed only before target-owned production state exists or after verified export/reconciliation;
otherwise it must fail closed. `SourceAccount.collection_options`, Source schedule and account cursor compatibility
columns remain deprecated-but-present throughout R1; deletion is not part of this implementation.

## 12. Exact implementation file scope

Allowed new files:

- `alembic/versions/<real-next>_create_collection_targets.py`
- `src/market_intelligence/collection/target_configs.py`
- `src/market_intelligence/collection/target_repository.py`
- `src/market_intelligence/collection/adapter_factory.py`
- `src/market_intelligence/collection/control_plane.py`
- `src/market_intelligence/providers/credential_resolver.py`
- `tests/test_collection_targets_postgres.py`
- `tests/test_collection_control_plane_postgres.py`
- `tests/test_collection_control_plane_redis.py`
- `tests/test_collection_control_plane_tasks.py`

Allowed modifications:

- `src/market_intelligence/db/models.py`
- `src/market_intelligence/collection/{contracts,scheduler,runner,locking,retry}.py`
- `src/market_intelligence/tasks/{collection,celery_app}.py`
- `src/market_intelligence/providers/{registry,runtime}.py`
- `src/market_intelligence/pipeline/{provider_runtime,multi_provider_ingestion}.py`
- `src/market_intelligence/scheduler/multi_provider_runtime.py` only to remove collection authority after cutover;
  delivery behavior remains
- relevant config templates, existing collection/scheduler tests and approved project docs.

Not allowed: Provider route/field expansion, durable safe projection, Event/Evidence/Fact/AI modules, Telegram
message/routing expansion, Market Validation, new Provider, PR #39 files, credential files, live data.

## 13. Implementation batches and acceptance gates

| batch | scope | merge/acceptance gate |
|---|---|---|
| I | migration + ORM + typed config registry | exact schema/constraints; upgrade/downgrade/upgrade; legacy reconciliation; secret/config fail closed |
| II | target repository/factory/credential resolver + worker reload | static allowlist; task carries IDs only; worker-only credential; unknown/mismatch no network |
| III | scheduler/claim/lock/retry/run/cursor/health | multi-target concurrency, target isolation, budgets, pagination, restart/stale recovery |
| IV | shadow comparison + single-authority cutover + delivery separation | no dual collection; notification preserved; rollback drill; full regressions |

Each batch requires its own implementation review evidence inside the authorized implementation PR sequence.
No batch may activate production targets or perform bounded live verification without separate user authorization.

## 14. Required test matrix

- PostgreSQL: every column/enum/check/FK/unique/partial index/immutability trigger; source/account consistency;
  target/run provenance; migration reconciliation and ambiguous fail-closed.
- Redis: concurrent dispatch SET NX EX, owner-token acquire/renew/release/loss, rate-group cooldown, retry vs cadence,
  stale recovery, restart markers and TTL coverage.
- Celery: task payload allowlist, worker reload/version mismatch, Beat replay/process restart, single enqueue per slot,
  retry lineage and no serialized config/credential.
- concurrency: two same-provider targets both run; same target single owner; one failure does not block another;
  checkpoint compare-and-swap prevents stale overwrite.
- budgets: operation units and all request/page/runtime/byte ceilings; `has_more=true` continues only within budget;
  continuation persists at cap.
- cursor: strict, snapshot, compound, page, date-window, normal/backfill separation and revision cases.
- delivery: collection succeeds with Telegram missing/failed; Notification pending/retry/SENT dedup preserved; Event
  delivery cannot gate collection.
- migration: real-head linearity, upgrade/downgrade/re-upgrade, shadow comparison, cutover/rollback, no double head,
  no PR #39 Draft migration.
- regression: all current Provider/CollectionRunner/RawItem/Evidence/Content/Event/Scheduler/Telegram tests PASS;
  network tests mock-only and package review contains no secret/local data.

## 15. Docs Review acceptance

- [ ] Reviewer accepts global immutable `target_key` uniqueness and final schema.
- [ ] Reviewer accepts Source/Account/Target responsibilities and run-based RawItem provenance.
- [ ] Reviewer accepts the four exact v1 operation schemas and no expansion.
- [ ] Reviewer accepts target-owned scheduling/state, budget, cursor and recovery contracts.
- [ ] Reviewer accepts reuse of Notification/Outbox without R1 delivery schema changes.
- [ ] Reviewer accepts legacy migration, shadow/cutover/rollback and single scheduler authority.
- [ ] Reviewer accepts exact files, four implementation batches and test matrix.
- [ ] Foundation v2.3-FROZEN/R0 PASS is recorded, while R1 implementation remains unauthorized.
- [ ] PR #39 remains Draft and untouched.
- [ ] Docs-only validation PASS; no code/schema/runtime/request/credential action occurred.

## 16. Explicit non-goals

No operation expansion, durable safe projection, Event/Evidence/Fact/AI change, Market Validation, Recommendation,
Portfolio/Holding/Investment Plan, Provider addition, X, GDELT, NewsAPI.ai, streaming/webhook/event bus, live
migration, bounded live request or PR #39 work.
