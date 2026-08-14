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
| `config_revision` | bigint | no / `1` | target execution generation；monotonic、`>0` |
| `operation_config` | JSONB | no / `{}` | JSON object；typed non-secret allowlist |
| `status` | `collection_target_status` | no / `draft` | sole lifecycle gate；no redundant `enabled` |
| `cadence_seconds` | integer | no | `1..86400` |
| `batch_limit` | integer | no | operation unit；positive and registry ceiling |
| `max_requests_per_run` | smallint | no / `1` | schema `1..20`; initial v1 registry requires `1` |
| `max_pages_per_run` | smallint | no / `1` | initial v1 operations must equal `1` |
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

`operation_config_version` means only the typed decoder/schema version. `provider_contract_version` means only the
adapter contract version. `config_revision` is the target's monotonic execution generation. Every change to
operation config, cadence, budget, operation/schema/contract version, cursor strategy/version, collection/backfill/
revision policy, rate-limit group, priority or other execution semantics must atomically increment
`config_revision`; status-only pause/resume also increments it so already-dispatched work becomes stale. Revision
rollback is a new forward revision restoring reviewed values, never decrementing the generation.

Dispatch identity, Celery payload and Redis marker include the exact `config_revision`. Worker DB reload compares
exact equality before credential resolution or network. A mismatch, concurrent edit, pause/resume or rollback makes
the stale task fail closed without request. The update service uses row lock or compare-and-swap
`WHERE config_revision=:expected`, so concurrent writers cannot publish the same next generation.

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

PostgreSQL must also enforce RawItem→Run provenance rather than relying on service code:

- add `UNIQUE NULLS NOT DISTINCT(collection_runs.id,source_id,source_account_id)` for identity support;
- add a deferrable PostgreSQL constraint trigger requiring each RawItem's source to equal its CollectionRun source
  and `RawItem.source_account_id IS NOT DISTINCT FROM CollectionRun.source_account_id`. A normal composite FK alone
  is insufficient because `MATCH SIMPLE` would skip the nullable account check; a composite FK may be supplemental
  for non-null rows but cannot replace the trigger;
- add immutable constraint triggers for CollectionRun `target_id`, `source_id`, `source_account_id` and `run_mode`;
- before enabling constraints, migration scans every historical RawItem/Run and Run/Target tuple. Any mismatch aborts
  with a value-free audit count/code; it must never guess, rewrite or silently repair provenance.

`ContentItem` and `EvidenceItem` currently copy source/account provenance. R1 regression checks their existing FK and
write-path consistency but does not add new cross-table constraints outside the control-plane migration. A complete
DB-level audit/constraint decision for `ContentItem/EvidenceItem ↔ RawItem/Run/Target` is a mandatory R2 durable-safe-
projection prerequisite and R8 factual-completeness prerequisite; R2 cannot start without recording a zero-mismatch
audit or an explicitly reviewed fail-closed remediation plan.

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

### 6.1 Initial pagination capability

`pagination_capability` is a registry property, **not a DB column or mutable target value**, so there is one
authority. All four v1 operations declare `pagination_capability=none`; registry validation requires
`max_pages_per_run=1` and `max_requests_per_run=1`:

- Marketaux currently always requests `page=1`;
- EIA has no offset/continuation implementation;
- SEC reads only `submissions.recent` and does not follow historical files;
- Finnhub quote is a single snapshot and is not pageable.

The generic control-plane interface may model future continuation, but it must not issue a second request for these
operations. If an adapter reports `has_more=true`, the run persists the accepted first batch and uses existing
persistent fields as the sole coverage state: `CollectionRun.status=partial`, `error_code=coverage_incomplete`,
`error_message_redacted='provider continuation is unsupported for this operation'`; target health becomes
`degraded`, `last_error_code=coverage_incomplete`, `next_retry_at=NULL`, and `next_due_at` advances by normal cadence.
This is a known coverage limitation, not an operational failure: `consecutive_failures` does not increment and
complete-success `last_success_at` does not update. The observation cursor may advance to the maximum stable identity
persisted in the accepted batch, but complete-window watermark does not advance. The run is never succeeded,
complete or no-new-items, and restart can query the PARTIAL run/target health from DB. It must not repeat page 1.
Real Marketaux page, EIA offset and SEC history continuation are R3–R5 operation expansion and are not implemented
by R1. No current Provider can claim pagination recovery acceptance.

## 7. Request budgets and rate-limit groups

Effective limits are the minimum of registry hard ceiling, verified plan ceiling, target value and worker emergency
ceiling. Each run enforces requests, pages, batch items, response bytes, request timeout and wall-clock runtime.
Exceeded response bytes abort before parsing/persistence with safe error. No infinite pagination/retry.

Initial `rate_limit_group` values are static provider groups (`marketaux:default`, `finnhub:default`, `eia:default`,
`sec-edgar:public`). Future account-specific groups use an opaque internal account UUID, never credential/contact.
Redis token/cooldown keys are group-scoped. 429 honors bounded Retry-After; group cooldown does not block targets
outside the group.

### 7.1 Enforceable response-byte boundary

`ProviderTransportRequest` gains required `max_response_bytes`; the factory copies the effective target/registry
budget into every request. `HttpxProviderTransport` must use HTTPX streaming rather than `response.json()` first.
If a valid nonnegative `Content-Length` exceeds the budget, reject before body read. Otherwise read bounded decoded
bytes (`aiter_bytes`, after HTTP content decoding), stop as soon as accumulated decoded bytes exceed the budget, and
only then JSON-decode the bounded buffer. The normative budget is decoded response-body bytes; Content-Length is an
early wire-length guard, not the final accounting source.

Over-limit response is not parsed or persisted and returns safe code `provider_response_too_large`, non-retryable
for the unchanged target budget. No length, payload fragment, URL or header value enters error output. Mock transport
must exercise declared-too-large, streaming overflow, exact-boundary, missing/invalid Content-Length and no-parse/
no-persistence behavior.

## 8. Generic future cursor interface, current non-pageable operations, backfill and revision

- strict cursor advances only to `candidate>current`; equal is operation-defined duplicate/no-new, backward fails;
- snapshot cursor: equal=`NO_NEW_ITEMS`, newer advances, older fails;
- compound order is lexicographic stable tuple; missing tie-breaker fails closed;
- the generic interface requires future pageable operations to persist each page before continuation checkpoint and
  advance final watermark only after a bounded window completes; **none of the initial v1 operations may exercise
  this interface in R1**;
- for initial operations, `has_more=true` persists the accepted batch but records incomplete/unsupported coverage,
  leaves final coverage watermark unchanged and stops after the single request;
- normal and `manual_bounded` backfill use separate `run_mode` cursor rows; scheduler creates only normal runs;
- backfill requires a future explicit command/review and bounded start/end; no generic historical backfill in R1;
- revision operations retain official identity+revision marker; stale revision never overwrites newer factual state.

## 9. Scheduler, dispatch, worker and recovery contract

1. Scheduler keyset-pages active targets ordered by effective eligible time, priority, id.
2. It validates Source kill switch/authorization and Account enabled/identity before claim.
3. Due time is `next_retry_at` when set, else `next_due_at`; permanent config errors do not fast-loop.
4. Dispatch identity is `target_id + scheduled_slot + run_mode + config_revision`; Redis
   `SET NX EX` TTL covers slot+deadline+retry window. Celery task id derives deterministically.
5. Task payload is only `target_id`, exact `config_revision`, scheduled slot, run mode and dispatch id—never config
   or credential.
6. Worker reloads target/source/account/config, verifies exact revision and eligibility, resolves an allowlisted
   factory, then credential. Paused/changed/unauthorized/stale-revision target fails closed before credential/network.
7. Worker acquires Redis owner-token target lock; renew/release compare owner token. Lost lock prevents checkpoint.
8. Each successful batch atomically writes RawItem/run counters/cursor. Cursor never advances on persistence error.
9. An ordinary retryable failure may preserve the same CollectionRun attempt lineage only while that run remains
   `RUNNING retry-pending`; it sets `next_retry_at` using bounded RetryPolicy/Retry-After. A terminal FAILED run is
   never reopened. Success clears retry and advances normal cadence from completion.
10. stale recovery requires expired/missing owner lock and no live retry marker; it marks run failed safely, never
    advances cursor, and makes target eligible according to classified recovery policy.

One target failure/lock/retry cannot affect another target. Source success/failure fields become display-only
aggregates and never gate target scheduling.

### 9.1 Config revision and in-flight run state machine

- edit, pause, block, retire and rollback-as-forward-revision atomically increment `config_revision` under CAS;
- pause clears `next_retry_at`; block/retire clear it and prevent automatic dispatch; retired is terminal;
- stale queued/retry tasks delete only their own revision-scoped Redis retry/dispatch marker after owner-token/CAS
  verification. They never delete a newer revision marker;
- if state changes after dispatch but before run creation, worker exits `stale_target_revision` with no run;
- if a run exists or worker holds lock when revision changes, the old worker must detect revision before every request
  and checkpoint. It closes its run `FAILED`, safe code `target_revision_invalidated`, clears the old retry marker,
  releases the partial unique RUNNING row and owner lock, and cannot update target health/cursor via CAS;
- if revision changes while retry waits, stale retry closes the existing RUNNING run the same way before any request;
- a new revision may create a new run only after no RUNNING row remains for target/run_mode and the old owner lock is
  absent. Recovery may close an orphaned stale run but cannot advance cursor;
- old worker updates target/run/cursor only with `WHERE target_id AND config_revision AND owner_token` equivalent
  guards, preventing it from overwriting the newer generation.

Manual pause without an in-flight run creates no CollectionRun. Manual block/retire requires value-free AuditLog.
Rollback restores old semantic values as a new revision and follows the same drain/closure rules.

### 9.2 Exact dispatch eligibility

Scheduler and worker call the same pure eligibility policy:

- `Source.enabled=true`;
- `Source.authorization_status` is exactly `authorized` or `implemented`;
- target `status=active` and its due/retry time is eligible;
- if `source_account_id` is non-null, `SourceAccount.enabled=true`, it belongs to the same Source, and
  `identity_status=verified`;
- a source-level target (`source_account_id=NULL`) is allowed only when that Source has **no SourceAccount rows**;
  the presence of even disabled/unverified accounts makes source-level execution invalid until explicitly modeled;
- provider/operation/config/contract versions and Source `access_method` must match the static registry.

The scheduler applies this before dispatch, and the worker repeats it after reload. Any state/revision change after
dispatch blocks the task before credential resolution and network. `planned`, `access_tbd`, `degraded`, `blocked`,
`disabled` Source authorization and `unverified`, `changed`, `disabled` account identity are never executable.

## 10. Delivery decoupling and Notification/Outbox decision

Current code is not yet independent: `multi_provider.telegram.run` performs collection, calls `deliver_new()` to
create/claim Notification rows, and sends Telegram in one cycle. R1 **reuses existing `notifications` and
`outbox_messages` without schema extension**, but changes orchestration to this final flow:

1. Collection/Content downstream transaction deterministically inserts a `PENDING` Notification intent using the
   existing unique `dedup_key`. If the Content transaction cannot include it atomically, an idempotent reconciliation
   task polls eligible Content rows and inserts missing intents; this is the required equivalent recoverable boundary.
2. A successful collection run never reads Telegram credential and never sends Telegram.
3. New Celery task `notification.telegram.deliver` polls DB and atomically claims `PENDING`, retryable `FAILED`, and
   stale `SENDING` rows using `FOR UPDATE SKIP LOCKED`/conditional status updates. It alone reads Telegram runtime
   credentials and invokes transport.
4. Missing credential leaves rows `PENDING` (or preserves existing FAILED/SENDING state) with safe delivery status;
   transport failure updates Notification retry state only. Neither changes CollectionRun, cursor or target health.
5. Beat/task loss is recovered by DB polling; delivery intent is not dependent on an in-memory item list or current
   collection cycle.

`multi_provider.telegram.run` is retired as a collection authority at cutover. Its collection portion is replaced by
the unified target dispatcher/worker; its delivery portion is replaced by `notification.telegram.deliver`. During
cutover, stop and drain the legacy combined task before enabling the delivery-only Beat. A Redis/DB delivery claim
plus Notification conditional state transition prevents concurrent claims, but old and new Beats must never be
authoritative simultaneously.

If collection/downstream persistence succeeds but Notification intent creation fails, collection remains succeeded;
an existing `AuditLog` records only safe code `notification_intent_pending_recovery` against the ContentItem, and
the idempotent reconciler finds eligible ContentItems with no matching dedup key and creates the missing PENDING row.
It must not re-collect or roll back the cursor. The deterministic dedup key makes retries harmless.

Existing Notification is the sole Telegram state machine and already carries dedup, status, retry, schedule and
failure fields. Existing Outbox remains a generic transactional-message facility; using it as a second Telegram queue
would create competing claims/status and is forbidden. No Notification/Outbox schema change is required by this
contract. If implementation proves the atomic/reconciliation boundary impossible with current columns, it must stop
and return for schema re-review rather than silently expanding migration scope.

### 10.1 Exact intent/reconciler candidate contract

- approved providers are exactly `marketaux`, `finnhub`, `eia`, `sec_edgar`;
- eligible `ContentKind` is `article`, `feed_entry`, or `official_release`; `x_post` and unknown kinds are excluded;
- Source must pass R1 execution authorization and Content must have nonblank safe title plus `source_published_at`;
  canonical URL is optional, but when present must be sanitized public http(s) without userinfo/secret marker;
- policy is fixed to `policy_rule_id=spec-0038-multi-provider-telegram`, `policy_version=1`,
  `channel=telegram_push`, `payload_version=1`, priority P3;
- deterministic dedup key remains `{provider}:telegram:{content_item_id}` and the existing unique index is authority;
- Evidence-only rows, missing Content, unsafe/missing display, Event rows, non-Telegram policy and non-approved
  provider content never create an intent.

Cutover watermark is stored in existing `system_metadata` key `notification.intent.cutover.v1`; its value is the
stable tuple `<content_items.created_at UTC ISO8601>|<content_items.id UUID>` (well below 500 chars). It is written
exactly once in the same maintenance transaction that records legacy combined task drain complete, immediately
before enabling the new intent producer/reconciler. Candidate ordering is `(created_at,id)` ascending. Only Content
strictly after this watermark and satisfying the exact policy above is eligible. No implicit per-Content override
exists. Historical Content is not backfilled by default; policy version changes do not reactivate it.
Any bounded historical replay requires separate user authorization and its own reviewed command.

The reconciler first polls unresolved `AuditLog.action=notification_intent_recovery` rows, then performs a bounded
missing-dedup scan strictly after the cutover watermark (default 100, hard max 500), keyset ordered by
`(created_at,id)`. It uses `FOR UPDATE SKIP LOCKED` or unique-key insert conflict handling for concurrent workers.
Restart repeats from DB and the dedup key absorbs replay. Audit recovery rows use
`AuditLog.action=notification_intent_recovery`,
`target_type=content_item`, `target_id=<ContentItem.id>`, and value-free `after` metadata containing only policy id/
version, status and safe error code—never title/URL/content. After the Notification exists (created or duplicate),
the same transaction appends `AuditLog.action=notification_intent_recovery_resolved`, the same target, and
`actor_id=<original recovery AuditLog.id>`; an unresolved row is one with no such resolution row. This append-only
closure avoids a new column/schema and prevents permanent rescanning. A failed attempt remains unresolved.

If projection sidecar/downstream projection fails and no ContentItem exists, record safe code
`downstream_projection_incomplete` against the run/RawItem audit. The R1 reconciler must not read RawItem payload,
provider projection sidecar or raw response to reconstruct Content; R2 owns durable projection recovery.

## 11. Legacy migration, shadow, cutover and rollback

Migration must be additive and based on the implementation branch's real head (currently `0005`; never PR #39's
Draft `0006/0007`). It uses two serial revisions; deployment never assumes migration and worker code are atomic.

### 11.1 Expand/contract deployment

| phase | action | authoritative path | rollback condition |
|---|---|---|---|
| 0 | stop and continuously hold all legacy collection/stale-recovery tasks that may create or modify CollectionRun, CollectionCursor or RawItem; drain to zero RUNNING. Delivery-only Telegram work may continue only if it cannot invoke collection or mutate those tables | none (maintenance window) | resume unchanged legacy tasks only before Migration A/backfill starts |
| 1 / Migration A | create enums/target table; add nullable target/run/cursor compatibility fields, indexes and non-destructive checks | legacy | downgrade A only if no target-owned writes |
| 2 | with legacy collection still stopped, perform deterministic target/backfill and historical Run/RawItem/Cursor consistency scan | none | abort on mismatch; keep tasks stopped and targets paused/blocked |
| 3 | deploy compatible runtime while collection remains stopped; verify zero RUNNING legacy runs, zero unmapped/new NULL target_id runs/cursors, and exact backfill reconciliation counts | none | keep maintenance hold; deploy previous compatible worker only if Migration A downgrade remains safe |
| 3A | only after phase-3 verification, resume legacy authority through the compatible runtime that writes target_id and transactionally dual-writes eligible target+legacy cursor | legacy compatible runtime | stop/drain compatible runtime; nullable expand schema remains |
| 4 | shadow read-only comparison of due/config/cursor; no request, enqueue or write | legacy | disable shadow |
| 5 | reviewer approves only rollback-eligible targets; set paused→active with new config_revision and cutover-ready next_due_at | legacy | return target to paused with forward revision |
| 6 | stop/drain legacy combined collection task and all in-flight runs; write Notification cutover watermark | none (maintenance boundary) | restart legacy before unified enable |
| 7 | enable unified scheduler/worker as sole collection authority and delivery-only Beat as sole Telegram claimer | unified | stop/drain unified, reconcile cursors, restore legacy |
| 8 | reconcile target/run/cursor/notification counts and operate through rollback window | unified + cursor dual-write | rollback only after verified target→legacy reconciliation |
| 9 / Migration B | after rollback window exit, scan zero null/mismatch; add NOT NULL/final unique/FK/triggers | unified | forward recovery only; no runtime rollback |

Migration B is never deployed while an old worker can run. Compatibility tests must prove old worker+Migration A and
new worker+Migration A. There is no supported old worker+Migration B combination.

R1 chooses the continuous maintenance hold for phases 0–3; it does not claim a high-watermark catch-up protocol.
Delivery-only Telegram tasks may remain live only after a source audit proves they cannot invoke collection,
stale recovery or writes to CollectionRun/CollectionCursor/RawItem. Otherwise they are drained too.

### 11.2 Cursor rollback contract

R1 chooses **transactional dual-write during the rollback window**. Every committed normal batch updates the
target-owned cursor and compatible legacy account cursor in the same transaction; normal and `manual_bounded`
backfill target cursors remain separate, and backfill never overwrites legacy normal cursor. Continuation,
complete-window watermark and revision marker are target-owned; because initial operations are non-pageable,
continuation is null. The legacy cursor receives only the committed observation position/watermark/revision values
its existing codec can represent.

Legacy rollback identity is exactly `(source_account_id, legacy_cursor_type)`. During the rollback window, only a
target with a one-to-one representable legacy identity may be activated, enforced by DB uniqueness for active rows
plus service validation before activation/cutover. At most one active target may own any such pair. Two targets must
never share, overwrite or compete for one legacy cursor. Rollback-window multi-target acceptance therefore covers
different accounts or other distinct legacy-representable identities—not two targets under the same account using
`provider_cursor_v1`.

True same-SourceAccount multi-target activation is deferred until the rollback window is explicitly closed,
Migration B is complete, legacy dual-write is disabled and operation is forward-recovery-only. Migration B acceptance
then tests independent target-owned cursors for same-account targets. If the business requires that topology during
the rollback window, R1 must abandon old-runtime rollback and return for a new review; it is not the default contract.

Before rollback, stop/drain unified workers and compare every active target position with its legacy cursor. Any
unrepresentable continuation/revision or mismatch blocks rollback. After restoring legacy, run duplicate/gap checks
over stable provider identity and watermark boundaries before requests resume. The rollback window ends only after
at least two normal cadences per active target, zero mismatch/incomplete reconciliation, notification recovery PASS,
and explicit reviewer approval. Migration B then disables legacy cursor writes; runtime rollback is no longer
allowed—only forward recovery. Legacy columns remain for audit until a later cleanup SPEC.

### 11.3 Deterministic legacy identity and exact mapping

Target key algorithm is fixed:

- source-level: `legacy.<provider>.source.<source_uuid_lower_hex_with_hyphens>`;
- account-level: `legacy.<provider>.account.<source_account_uuid_lower_hex_with_hyphens>`;
- `<provider>` is the allowlisted normalized `Source.access_method` (`[a-z0-9_]+`), not user data;
- result matches `^[a-z0-9][a-z0-9._-]{0,159}$`, contains no query/config/identity/secret, and is at most 100 chars;
- same rows always yield the same key. UUID identity gives global uniqueness; any unique conflict or malformed
  provider blocks migration with safe code `legacy_target_key_conflict`, never suffixes/guesses.

| legacy source | operation/config mapping | initial target state |
|---|---|---|
| fake source-level/account | `fake/fake_sequence`; only currently allowlisted synthetic behavior fields | paused; strict cursor |
| Marketaux account | `marketaux/news_all`; exact v1 query/language/symbols validation | paused; compound cursor |
| Finnhub account | `finnhub/quote`; exact one symbol | paused; compound strict cursor |
| EIA account | `eia/electricity_retail_sales`; dataset must equal electricity | paused; snapshot compound cursor |
| SEC account | `sec_edgar/submissions_recent`; exact ticker+CIK | paused; snapshot/revision compound cursor |

Unknown provider, multiple operation interpretations, missing required config, source-level real-provider row, or
account/source inconsistency maps to `blocked`, not a guessed operation. Every created target starts
`config_revision=1`, `health_status=unknown`, `consecutive_failures=0`, `next_retry_at=NULL`, `last_attempt_at=NULL`,
`last_success_at=NULL`. `next_due_at` is the migration timestamp but paused/blocked status prevents dispatch; explicit
activation sets a reviewed next_due_at and increments revision. Cadence is the positive legacy Source schedule when
present, otherwise the already-configured provider cadence setting; if neither is valid, block. Rate groups are the
four static groups in §7 (fake=`fake:test`). Budgets are exact §6/§7 v1 hard values, max pages/requests=1. No smoke
value becomes authorization.

### 11.4 Normative state transition matrix

This table is the sole normative state authority; prose elsewhere must defer to it. `LA` means set
`last_attempt_at=now`; `LS` means set `last_success_at=now`; `ND` means normal cadence from completion; `NR` means
bounded retry time; `—` means unchanged/not applicable. Source aggregate is display-only and never gates dispatch.

| event | CollectionRun | target status | health | last attempt | last success | failures | last error | next due | next retry | cursor/watermark | Source aggregate | recovery |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| success with items | SUCCEEDED | active | healthy | LA | LS | 0 | NULL | ND | NULL | observation+complete watermark advance atomically | recompute display | automatic cadence |
| no-new-items | SUCCEEDED | active | healthy | LA | LS | 0 | NULL | ND | NULL | unchanged | recompute display | automatic cadence |
| coverage incomplete | PARTIAL | active | degraded | LA | unchanged | unchanged | coverage_incomplete | ND | NULL | observation may advance; complete watermark unchanged | recompute display | normal cadence / later operation SPEC |
| timeout/429/5xx retry | RUNNING retry-pending | active | degraded | LA | unchanged | +1 | classified safe code | unchanged | NR | unchanged | recompute display | automatic bounded retry |
| retry exhausted | FAILED, or PARTIAL only if items already committed | active | degraded | LA | unchanged | +1 | retry_exhausted | ND | NULL | last committed checkpoint only | recompute display | normal cadence/manual review |
| config/schema invalid | no run if precheck; otherwise FAILED | blocked | blocked | LA only if run | unchanged | unchanged | config_invalid | unchanged | NULL | unchanged | recompute display | manual fix + forward revision |
| credential missing | no run | active | degraded | LA | unchanged | unchanged | credential_missing | ND | NULL | unchanged | recompute display | credential restored: next normal due; immediate retry only by reviewed manual action |
| Source authorization change | no run; in-flight FAILED | blocked | blocked | LA if in-flight | unchanged | unchanged | source_unauthorized | unchanged | NULL | unchanged | recompute display | manual authorization + forward revision |
| Account identity change | no run; in-flight FAILED | blocked | blocked | LA if in-flight | unchanged | unchanged | account_identity_invalid | unchanged | NULL | unchanged | recompute display | verify identity + forward revision |
| DB/checkpoint failure | RUNNING retry-pending; on exhaustion FAILED, or PARTIAL if items committed | active | degraded | LA | unchanged | +1 | database_unavailable | unchanged while retrying; ND at terminal completion | NR while retrying; NULL terminal | last committed checkpoint only | recompute display | ordinary retry while RUNNING; normal cadence after terminal |
| lock lost | old run immediately FAILED and RUNNING uniqueness released | active | degraded | LA | unchanged | +1 | lock_lost | unchanged | NR | last committed checkpoint only | recompute display | retry creates a new CollectionRun; audit links safe error+dispatch identity only |
| stale recovery | FAILED | active unless separately blocked | degraded | unchanged | unchanged | +1 | stale_run | unchanged | classified NR/ND | unchanged | recompute display | automatic classified recovery |
| manual pause | no run; in-flight FAILED | paused | unchanged | —/LA if in-flight | unchanged | unchanged | target_paused if in-flight | unchanged | NULL | unchanged | recompute display | manual resume + forward revision |
| manual block | no run; in-flight FAILED | blocked | blocked | —/LA if in-flight | unchanged | unchanged | target_blocked | unchanged | NULL | unchanged | recompute display | manual reviewed unblock |
| manual retire | no run; in-flight FAILED | retired | blocked | —/LA if in-flight | unchanged | unchanged | target_retired | unchanged | NULL | unchanged | recompute display | none; create new target if later approved |
| config revision invalidation | no run pre-create; existing run FAILED | new revision status | new revision health/unknown | LA only if existing run | unchanged | unchanged | target_revision_invalidated | new revision value | NULL old revision | unchanged | recompute display | close stale run; new revision may run |

Credential/config/authorization failures do not fast-loop. Coverage limitation does not increment failures. Only a
complete success or valid no-new-items updates `last_success_at`; partial/failed/blocked outcomes never do.
The same-run retry lineage applies only while a run is `RUNNING retry-pending`; lock-lost, stale-recovered and
config-invalidated terminal runs are immutable, and any later retry creates a new CollectionRun. No parent-run schema
is added; safe error code and dispatch identity provide the audit link.

## 12. Exact implementation file scope

Allowed new files:

- `alembic/versions/<real-next>_expand_collection_targets.py`
- `alembic/versions/<following>_contract_collection_targets.py`
- `src/market_intelligence/collection/target_configs.py`
- `src/market_intelligence/collection/target_repository.py`
- `src/market_intelligence/collection/adapter_factory.py`
- `src/market_intelligence/collection/control_plane.py`
- `src/market_intelligence/providers/credential_resolver.py`
- `src/market_intelligence/notifications/intent.py`
- `src/market_intelligence/notifications/delivery.py`
- `src/market_intelligence/tasks/notification_intent_reconcile.py`
- `src/market_intelligence/tasks/notification_delivery.py`
- `tests/test_collection_targets_postgres.py`
- `tests/test_collection_control_plane_postgres.py`
- `tests/test_collection_control_plane_redis.py`
- `tests/test_collection_control_plane_tasks.py`

Allowed modifications:

- `src/market_intelligence/db/models.py`
- `src/market_intelligence/collection/{contracts,scheduler,runner,locking,retry}.py`
- `src/market_intelligence/tasks/{collection,celery_app}.py`
- `src/market_intelligence/providers/{contracts,http_transport,registry,runtime}.py`
- `src/market_intelligence/pipeline/{provider_runtime,multi_provider_ingestion}.py`
- `src/market_intelligence/scheduler/multi_provider_runtime.py` only to remove collection authority after cutover;
  delivery behavior remains
- `src/market_intelligence/scheduler/{multi_provider,marketaux_telegram}.py` only to extract/reuse Notification
  intent/claim logic and retire combined collection+delivery authority
- `src/market_intelligence/tasks/{multi_provider_scheduler,marketaux_telegram}.py` to retire combined tasks
- relevant config templates, transport/adapter/collection/scheduler/notification tests and approved project docs.

Not allowed: Provider route/field expansion, durable safe projection, Event/Evidence/Fact/AI modules, Telegram
message/routing expansion, Market Validation, new Provider, PR #39 files, credential files, live data.

## 13. Implementation batches and acceptance gates

| batch | scope | merge/acceptance gate |
|---|---|---|
| I-A | Migration A nullable expand + ORM compatibility + typed config registry | real-head linearity; old worker+A and new worker+A; deterministic target mapping; historical value-free audits; no activation |
| II | target repository/factory/credential resolver + worker reload | static allowlist; task carries IDs only; worker-only credential; unknown/mismatch no network |
| III | scheduler/claim/lock/retry/run/cursor/health | rollback-eligible multi-target isolation, state matrix, budgets, pagination-capability-none, restart/stale recovery |
| IV | Notification intent/reconciler/delivery-only task + shadow/single-authority cutover | cutover watermark; no historical default; no dual collection/delivery claim; rollback drill; full regressions |
| I-B | Migration B final constraints after rollback-window approval | zero null/mismatch audit; new worker+B only; final constraints/triggers; forward recovery only |

These are five acceptance batches (four functional batches plus Migration B finalization). Each batch requires its
own implementation review evidence inside the authorized implementation PR sequence.
No batch may activate production targets or perform bounded live verification without separate user authorization.

## 14. Required test matrix

- PostgreSQL: every column/enum/check/FK/unique/partial index/immutability trigger; source/account consistency;
  target/run provenance; null-safe RawItem/Run equality; Content/Evidence zero-mismatch prerequisite audit;
  deterministic legacy target mapping; Migration A/B reconciliation and ambiguous fail-closed.
- Redis: concurrent dispatch SET NX EX, owner-token acquire/renew/release/loss, rate-group cooldown, retry vs cadence,
  stale recovery, restart markers and TTL coverage; stale revision can remove only its own marker and cannot consume
  a newer revision retry/dispatch key.
- Celery: task payload allowlist, exact config_revision, worker reload/revision mismatch before credential, Beat
  replay/process restart, single enqueue per slot, retry lineage and no serialized config/credential.
- concurrency: two same-provider targets both run; same target single owner; one failure does not block another;
  checkpoint compare-and-swap prevents stale overwrite; concurrent config edit, duplicate dispatch, pause/resume and
  rollback generation make stale tasks fail before credential/network. During rollback, multi-target cases use
  distinct legacy cursor identities and activation rejects duplicate `(source_account_id,legacy_cursor_type)`;
  after Migration B, same-account targets prove independent target cursors.
- budgets/pagination: operation units and request/runtime/byte ceilings; all initial operations require one page;
  `has_more=true` records truncated/incomplete/unsupported and never repeats page 1 or claims complete. Generic
  continuation contract is unit-tested without claiming Provider recovery support. Transport rejects oversized
  Content-Length and streamed decoded bodies before JSON parsing/persistence, including exact-boundary tests.
- coverage state: DB restart observes PARTIAL/coverage_incomplete, degraded target, unchanged complete watermark,
  normal cadence, no failure increment and no false complete/succeeded/no-new state.
- cursor: strict, snapshot, compound, normal/backfill separation and revision cases; no v1 Provider page recovery;
  target↔legacy transactional dual-write, mismatch blocking, rollback reconciliation and rollback-window exit.
- eligibility: scheduler and worker share exact Source authorization/enabled, Account identity/enabled/source-level,
  target status/revision and registry rules; post-dispatch state change prevents network.
- delivery: deterministic PENDING intent, atomic-or-reconcilable boundary, reconciler after intent failure,
  delivery-only DB polling/claim, missing credential preservation, retry/SENT dedup, Beat restart recovery and no
  simultaneous old/new delivery claim; exact candidate policy, cutover watermark, bounded keyset recovery and no
  default historical backfill; unresolved recovery AuditLogs are handled first and append a resolved AuditLog on
  success so they do not scan forever; Event delivery cannot gate collection.
- state machine: every row in §11.4, including config invalidation before/after run creation, pause/block/retire,
  lock loss terminal-run/new-run retry, stale recovery, DB/checkpoint exhaustion normal cadence, credential missing
  normal-cadence recovery, retry exhaustion, authorization changes and complete-success timestamps.
- migration: real-head linearity, A upgrade/downgrade/re-upgrade, old worker+A, new worker+A, shadow comparison,
  phase-0 continuous collection-task drain, zero-run/null/count phase-3 verification, transactional cursor rollback
  drill, rollback eligibility uniqueness, B finalization with new worker only, no double head and no PR #39 migration.
- regression: all current Provider/CollectionRunner/RawItem/Evidence/Content/Event/Scheduler/Telegram tests PASS;
  network tests mock-only and package review contains no secret/local data.

## 15. Docs Review acceptance

- [ ] Reviewer accepts global immutable `target_key` uniqueness and final schema.
- [ ] Reviewer accepts monotonic `config_revision` and stale-task race semantics.
- [ ] Reviewer accepts Source/Account/Target responsibilities and run-based RawItem provenance.
- [ ] Reviewer accepts the four exact v1 operation schemas and no expansion.
- [ ] Reviewer accepts target-owned scheduling/state, budget, non-pageable v1 cursor and recovery contracts.
- [ ] Reviewer accepts Notification intent/reconciler/delivery-only flow and Outbox non-use without schema changes.
- [ ] Reviewer accepts legacy migration, shadow/cutover/rollback and single scheduler authority.
- [ ] Reviewer accepts exact files, five acceptance batches (four functional + Migration B finalization) and tests.
- [ ] Foundation v2.3-FROZEN/R0 PASS is recorded, while R1 implementation remains unauthorized.
- [ ] PR #39 remains Draft and untouched.
- [ ] Docs-only validation PASS; no code/schema/runtime/request/credential action occurred.

## 16. Explicit non-goals

No operation expansion, durable safe projection, Event/Evidence/Fact/AI change, Market Validation, Recommendation,
Portfolio/Holding/Investment Plan, Provider addition, X, GDELT, NewsAPI.ai, streaming/webhook/event bus, live
migration, bounded live request or PR #39 work.
