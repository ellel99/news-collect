# Changelog

## Unreleased — SPEC-0037 Multi Provider Runtime Verification

- 修复 SEC EDGAR snapshot polling：same latest cursor 正常结束为 no-new-items，newer cursor 推进，
  older cursor fail closed；Marketaux/Finnhub/EIA 保持 strict cursor contract。
- runtime summary 新增 content-free collection status/error diagnostics；本轮 review fix 未请求任何
  Provider API，等待用户进行一次 SEC post-fix live verification。
- 将 SPEC-0036 标记为 PR #35 approved/completed，并激活 SPEC-0037。
- 新增统一 `multi_provider_runtime_smoke.py`，支持 inert dry-run、三家 doctor/bootstrap 与显式 execute。
- 固定 Finnhub → EIA → SEC 串行执行且每家最多一次 request；EIA/SEC bounded adapter 不分页。
- 本地 bootstrap/doctor 三家 PASS；首次 execute 因 process credential 全部 MISSING 在网络前安全
  BLOCKED，未读取 `.env`、未发出 Provider request、未写 live data。
- tests/CI mock-only；无 scheduler/Telegram、AI、投资建议、dedup/Event、migration/ORM/schema change。

## Unreleased — SPEC-0036 Multi-provider Ingestion

- 将 SPEC-0035 标记为 PR #34 approved/completed，并激活 SPEC-0036。
- 新增 Finnhub quote、EIA electricity、SEC EDGAR submissions adapters，复用现有 CollectionRunner、
  RawItem、provider mapping 与 EvidenceWriteService。
- 新增三个 default-dry-run/manual-execute smoke commands；tests/CI 只使用 mocked transports。
- 为三个 smoke 增加通用 target doctor/bootstrap：安全区分 missing/not-unique/disabled/unauthorized/
  account-missing，首次创建、重复幂等，多 target fail closed；bootstrap 不读 credential、不请求 API。
- 补充 SEC agent + contact 缺一 fail closed 与 mocked transport User-Agent 构造/不回显测试。
- SEC 只生成 metadata-only official-release ContentItem；不下载 filing body；Finnhub/EIA 保持 evidence-only。
- 无 scheduler/Telegram routing、AI、投资建议、formal dedup/Event、多余 Provider、migration/ORM/schema change。

## Unreleased — SPEC-0035 Minimal Scheduler for Marketaux + Telegram

- 将 SPEC-0034 标记为 PR #33 approved/completed，并激活 SPEC-0035。
- 新增 Celery Beat `marketaux.telegram.run` 与 manual smoke，复用已验证 Marketaux collection/evidence/
  ContentItem/Telegram components；默认 dry-run 不读 credential、不访问 runtime、不发送。
- 加固 Marketaux Telegram 投递：加入 FAILED bounded retry、stale SENDING 原子 recovery、retry
  exhaustion safe counts，并使历史 retry 独立于 current collection run；无 schema change。
- 复用既有 Notification unique dedup key 作为 ContentItem sent marker；Telegram failure 保留 failed
  safe state，不静默丢失；SENT 永不重发。
- mock-only tests；无 `.env`/raw response/live output、migration/ORM/schema change、AI、dedup/Event、
  Finnhub/EIA/SEC/X、多 Provider或 SPEC-0022。

## Unreleased — SPEC-0034 Alembic State Repair / Docker Startup Health

- 将 SPEC-0033 标记为 PR #32 与用户 post-fix live verification approved/completed，并激活 SPEC-0034。
- Git 历史与本地 schema inspection 确认早期 `0003` artifact 与当前 `0003` contract 漂移；新增幂等
  forward revision `0004`，补齐 composite provenance FK/index 与 raw reference secret-marker check，
  不改写 `0003`，不新增 table/column/entity。
- 当前 migration chain 为 `0001 -> 0002 -> 0003 -> 0004`；新增 safe Alembic code/database state doctor。
- 新增默认 dry-run、single-head-only、schema-compatible-only 的显式 repair guardrails；不接受任意 revision，
  不删除 database volume，不跳过 migrate，不改写既有 migration/ORM，也不新增 table/column/entity。
- 新增 Docker image rebuild、doctor、normal migrate、api health recovery runbook；无 `.env`/dump/secret、
  scheduler、AI、dedup/Event、多 Provider或 SPEC-0022。

## Unreleased — SPEC-0033 Marketaux Visible Feed + Manual Telegram Push

- 将 SPEC-0032 标记为 PR #31 与用户 post-fix live verification approved/completed，并激活 SPEC-0033。
- 在不修改 schema/ORM/migration 的前提下，将 adapter allowlisted title/public URL 作为 sanitized
  same-run sidecar metadata 写入现有 metadata-only ContentItem；新增 recent read-only Marketaux feed。
- 新增 default-dry-run Telegram preview 与 explicit manual push；credential 仅从 process environment
  读取，response body 不保存，message 仅含 title/source/time/link。
- 新增 mock-only provider/Telegram 与 PostgreSQL/Redis tests；无 scheduler、AI、investment advice、
  dedup/Event、Finnhub/EIA/SEC、`.env`/capture/raw response；SPEC-0022 未启动。
- PR #32 review fix 将 content-free evidence metadata 与 allowlisted visible display projection 分离，
  并将 Marketaux cursor 正规化为 provider 合同支持的 UTC datetime request 参数；不改变
  RawItemEnvelope contract。
- 新增 collection run safe diagnostics、feed `--require-items` fail-closed 验收语义，并确保
  Telegram feed empty 时不读 credential、不发送请求。用户首次 post-PR live run 失败
  记录为 `COLLECTION_CONTRACT_INVALID` / `provider_request_rejected`；尚未声称 post-fix live PASS。
- 另行记录本地 migrate `Can't locate revision identified by '0003'` 环境问题；本 PR
  不删除 DB volume，不修改 migration/ORM/schema。

## Unreleased — SPEC-0032 Marketaux Real Collection Pipeline

- 将 SPEC-0030 与 bundled SPEC-0031 标记为 PR #30 approved/completed，并激活 SPEC-0032。
- 新增 explicit runtime wiring、唯一 authorized Marketaux account target resolver 与 manual default-dry-run
  command；复用既有 registry/runner/RawItem/cursor/sidecar/Evidence Pipeline/Write Service。
- 新增 PostgreSQL/Redis mocked success、429/timeout、RawItem failure、duplicate、安全输出和 source audit
  tests；CI/pytest/package review 未请求真实 API。
- PR #31 review fix 新增 safe `--doctor` 与幂等 `--bootstrap-target`，解决干净本地 DB 缺少唯一
  Marketaux target 导致的 live execute BLOCKED，并细分 target diagnosis safe error；bootstrap 不读
  token、不请求 API，且无 migration/ORM/schema 变化。
- 无 `.env`/capture/raw response、scheduler/Telegram/AI/dedup/Event、Finnhub/EIA/SEC，亦无 migration/
  ORM/schema change；SPEC-0022 未启动。

## Unreleased — SPEC-0030 Marketaux Real Adapter Implementation

- 将 SPEC-0029 标记为 `Completed — Implementation Review approved`，并激活 SPEC-0030。
- 新增 repr-redacted runtime credential、allowlisted httpx transport 与 Marketaux real adapter boundary；
  官方 query token 只在最终 wire request 注入，不进入 provider-neutral params/result/error/cursor/RawItem。
- 新增 mock-only endpoint/request、200、secret echo、429/Retry-After、timeout、4xx/5xx、malformed、
  limit、cursor 与 source-audit tests；未执行真实 API。
- 无 `.env`/capture、Finnhub/EIA/SEC、scheduler、formal normalization/dedup/Event/AI/Telegram，
  无 migration/ORM/schema change；SPEC-0022 未启动，SPEC-0005 X Source 范围不变。
- 按合并交付策略将 SPEC-0031 bounded live smoke harness 纳入同一 PR #30：默认 dry-run，只有 manual
  `--execute` 才读取 process environment token；新增 missing-token/limit/mocked-execute/safe-output/source
  audit tests，CI/pytest/package review 未请求真实 API。

## Unreleased — SPEC-0029 End-to-End Mock Collection Evidence Pipeline

- 将 SPEC-0028 标记为 `Completed — Implementation Review approved`，并激活 SPEC-0029。
- 新增 provider result content-free observer、in-memory projection sidecar 与显式 end-to-end
  orchestrator；只有 CollectionRun/RawItem persistence 成功后才通过既有 trigger、Pipeline Service 和
  EvidenceWriteService 写 evidence_items。
- 新增 PostgreSQL/Redis mocked success/duplicate/persistence failure/missing/mismatch/429/timeout/
  authorization tests 与 source audit。
- 无 migration/ORM/schema change、真实 API、`.env`/capture、real adapter、scheduler、formal
  normalization/dedup/Event/AI/Telegram；SPEC-0022 未启动，SPEC-0005 X Source 范围不变。

## Unreleased — SPEC-0028 RawItem Evidence Projection Store and Pipeline Trigger

- 将 SPEC-0027 RawItem to Evidence Pipeline Orchestration 标记为 `Completed — Implementation Review
  approved`，并激活 SPEC-0028 Implementation Review。
- 新增 strict content-free in-memory projection store、既有 RawItem safe-field reader 与 explicit
  trigger；trigger 核对 provenance/identity/hash/reference 后调用 EvidencePipelineService。
- 新增 PostgreSQL success/duplicate/missing/malformed/mismatch/secret-safe tests 与 source audit；
  evidence_items 仍只通过 EvidencePipelineService/EvidenceWriteService 写入。
- 无 migration/ORM/schema change、真实 API、`.env`/capture、real adapter、scheduler、formal
  normalization/dedup/Event/AI/Telegram；SPEC-0022 未启动，SPEC-0005 X Source 范围不变。

## Unreleased — SPEC-0027 RawItem to Evidence Pipeline Orchestration

- 将 SPEC-0026 Collection Runner Adapter Registry Integration 标记为 `Completed — Implementation
  Review approved`，并激活 SPEC-0027 Implementation Review。
- 新增 content-free `EvidencePipelineService`，严格验证 Marketaux synthetic projection，通过既有
  provider mapper 生成 `CommonEvidenceEnvelope`，并只委托 `EvidenceWriteService` persistence。
- 新增 PostgreSQL success/duplicate/malformed/unknown/missing RawItem/provenance/secret-safe tests 与
  source audit；orchestration 不导入 ORM/SQLAlchemy，不绕过 Write Service 直接写 evidence_items。
- 无真实 API、`.env`/capture、real adapter、scheduler、formal normalization/dedup/Event/AI/
  Telegram；SPEC-0022 未启动，SPEC-0005 X Source 范围不变。

## Unreleased — SPEC-0026 Collection Runner Adapter Registry Integration

- 将 SPEC-0025 Provider Adapter Scaffold 标记为 `Completed — Implementation Review approved`，并
  激活 SPEC-0026 Implementation Review。
- 新增 provider-to-collection bridge 与显式 registry/mock transport injection；mocked Marketaux
  `RawItemEnvelope` 由 runner persistence 层写入 RawItem。
- RawItem 与 cursor checkpoint 复用同一事务，只有 persistence 成功才推进；provider safe errors
  映射为 redacted CollectionRun errors，unknown/unauthorized target fail closed。
- 保留 fake adapter regression；无真实 API、`.env`/capture、real adapter、EvidenceWriteService/
  evidence_items、scheduler、formal normalization/dedup/Event/AI；SPEC-0022 未启动。

## Unreleased — SPEC-0025 Provider Adapter Scaffold Implementation

- 将 SPEC-0024 Provider Adapter Integration Design 标记为 `Completed — Docs Review approved`，并
  激活 SPEC-0025 Implementation Review。
- 新增 Python provider contracts、fail-closed `ProviderAdapterRegistry`、network-free
  `MockProviderTransport` 与 Marketaux mocked response scaffold。
- Marketaux scaffold 只输出 content-free/sanitized metadata 和 `RawItemEnvelope`，覆盖 stable ID、
  deterministic cursor、record limit、secret isolation、429/timeout/provider error 分类。
- 新增 22 个 mock-only/source-audit tests；无真实 API、`.env`/capture/local evaluation、DB write、
  collection/scheduler、EvidenceWriteService、formal normalization/dedup/Event/AI；SPEC-0022 未启动。

## Unreleased — SPEC-0024 Provider Adapter Integration Design

- 将 SPEC-0023 Evidence Write Path implementation 标记为 `Completed — Implementation Review
  approved`，记录 PR #23 已交付安全 sanitize、provenance、provider-scoped idempotency、per-row
  savepoint、no silent data loss 与 PostgreSQL tests。
- 激活 docs-only SPEC-0024，设计 Marketaux、Finnhub、EIA Open Data、SEC EDGAR Adapter 的
  input/output、secret、rate/retry/timeout、cursor、RawItem retention、mapping handoff 与 collection
  runner integration 边界。
- 明确 Adapter 只负责 fetch + sanitized RawItemEnvelope，不得直接调用 EvidenceWriteService；本轮
  无 Python、Adapter/registry、collection、API/capture、migration/ORM/schema、正式
  normalization/dedup/Event 或 AI，SPEC-0022 未启动。

## Unreleased — SPEC-0023 Evidence Write Path Implementation

- 将 SPEC-0023 Docs Review 标记为 Completed，并激活独立 Evidence Write Path Implementation
  Review。
- 新增 `CommonEvidenceEnvelope` → `evidence_items` 安全写入 service/repository：unsafe reference
  置 `NULL` 并 blocked、provenance pre-check、provider-scoped duplicate/conflict、per-row savepoint
  与计数守恒 summary。
- 新增 synthetic-only PostgreSQL tests 与 source audit；无 migration/ORM/schema、API/capture、
  Adapter、collection、正式 normalization/dedup/Event 或 AI，SPEC-0022 未启动。

## Unreleased — SPEC-0023 Evidence Write Path Design

- 将 SPEC-0021 schema implementation 标记为 `Completed — Implementation Review approved`；
  PR #21 已交付 `evidence_items` migration/ORM/PostgreSQL tests、secret marker rejection 与
  raw item/source composite provenance consistency。
- 激活 docs-only SPEC-0023，设计 `CommonEvidenceEnvelope` 到 `evidence_items` 的输入/输出、
  sanitize/pre-check、provenance、provider-scoped conflict、per-row savepoint 和安全 summary。
- 明确 DB secret rejection 不是静默丢弃策略；未来实现必须安全替换/null unsafe reference、记录
  safe error，并逐 row 隔离失败且保证 summary 计数守恒。
- 本轮无 Python implementation、repository/service、migration/ORM/schema 变化、API/capture、
  Adapter、collection、正式 normalization/dedup/Event 或 AI；SPEC-0022 未启动。

## Unreleased — SPEC-0021 Evidence Persistence Schema Implementation

- 将 SPEC-0021 schema design 标记为 `Completed — Docs Review approved`，并激活独立
  implementation review。
- 新增单一 `0003` migration 与 `EvidenceItem` ORM，只创建 `evidence_items`，保留既有表。
- 增加 PostgreSQL schema、FK、allowlist、flags、JSONB shape、internal reference 与唯一性测试。
- 加固 `raw_payload_reference` secret marker 拒绝规则，并通过 composite FK 在数据库层强制
  `evidence_items` 与 `raw_items` 的 Source provenance 一致。
- 本轮无外部 API、capture/raw local data、Adapter、collection、正式 normalization、dedup、
  clustering、Event 或 AI；SPEC-0022 未启动，Foundation v2.1-FROZEN 不变。

## Unreleased — SPEC-0004 Documentation Preparation

### Documentation

- 将 SPEC-0020 标记 `Completed — Implementation Review approved`，并激活 docs-only SPEC-0021
  Evidence Persistence / DB Schema Design。
- 记录候选 `evidence_items` 字段、provenance、nullable content relation、禁字段、唯一性、license/
  retention 与 migration gate；本轮无 migration、ORM、DB/persistence implementation、API、capture、
  Adapter、collection、formal normalization/dedup、Event 或 AI。

- 将 SPEC-0019 标记 `Completed — Implementation Review approved`，并激活 SPEC-0020 Provider
  Evidence Mapping Scaffold。
- 新增四 Provider pure in-memory evidence mapping 与 mock-only tests；输出只包含 presence、count、
  hash 和 opaque refs，无 API、raw capture/local evaluation、Adapter、DB、collection、正式
  normalization/dedup、Event 或 AI。

- 将 SPEC-0018 标记 `Completed — Docs Review approved`，并激活 SPEC-0019 Evidence Contract
  Implementation Scaffold。
- 新增纯标准库 `market_intelligence.evidence` enums、frozen dataclasses、静态 mappings 与
  content-safe validation helpers；无 IO、网络、local capture、Adapter、DB、collection、正式
  normalization/dedup、Event 或 AI。
- 将 SPEC-0017 标记 Completed，记录四 Provider local replay-only verification：4 captures、19/19
  candidates、content values emitted=false；raw captures 仍仅在 gitignored local storage。
- 激活 docs-only `SPEC-0018 — Normalized Evidence Contract`，定义 common evidence envelope 与
  四类 provider-specific evidence payload contract；无代码、API、capture、Adapter、DB、
  collection、正式 normalization/dedup、Event 或 AI。
- 激活 `SPEC-0017 — Four Provider Replay Normalization Candidate`，并将经审核的 SPEC-0006
  标记 Completed；SPEC-0005 X Source 范围保持不变。
- 新增 mock-only `provider_normalize_replay.py`：从 gitignored local capture 生成 common
  envelope coverage 与 provider-specific content-free summary；无 API、capture、Adapter、DB、
  collection、正式 normalization/dedup 或 AI。
- 强化 EIA raw capture 写盘前 secret sanitization：递归移除回显 credential fields 和含 secret
  query marker 的字符串，清理失败则不写 capture；audit 继续 fail closed。
- 修复 SPEC-0006 review blockers：空 capture audit 目录现在 fail closed，replay summary 增加
  `replay_ready`，且该标记不代表 normalization 已实现。
- 激活 `SPEC-0006 — Raw Capture & Replay Evaluation`，保持 SPEC-0005 X Source 范围不变；
  原 Normalization/Dedup/Outbox Planned topic 保留并等待重新编号。
- 增加默认 dry-run 的本地 raw capture、content-free audit/replay summary 脚手架和 mock-only
  tests；`local_evaluation/` 全部 gitignored。
- 本轮没有真实 capture、外部 Provider 请求、raw capture 提交、Adapter、DB/migration/schema/
  ORM、collection、AI API 或投资建议。
- 增加 provider preflight scaffold：五个平台的官方合同索引、凭证空模板、bounded smoke
  runbook、placeholder-only provider 配置与默认 dry-run CLI。
- 增加完全 mock 的 preflight 单元测试；本轮未使用真实 API key、未请求外部 API、未运行
  真实 smoke、未实现 Adapter、未写数据库、迁移、schema/ORM 或 collection。
- Review 修正：CLI 支持默认根目录 `.env` 和显式 `--env-file`（OS 环境变量优先），EIA
  参数改为官方 `data[]=price`，PASS 判定收紧为 provider schema-aware。
- Preflight gate 调整：NewsAPI.ai 标记为 `future / blocked` 并禁止 `--execute`；当前顺序固定
  为 Marketaux → Finnhub → EIA → SEC，optional metadata 不阻塞最小 smoke。
- 记录用户单独授权的一次 Marketaux bounded smoke 的脱敏结构结果：HTTP 200、有效 JSON、
  `data` / `meta`、result count 1、预期字段与 rate/usage-limit headers，structural PASS。
- 未保存 token、完整 response、真实 title/body/URL 或 raw payload；Finnhub、EIA、SEC 未执行，
  Adapter、数据库、迁移、schema、ORM 和 collection 均未开始。
- 后续在用户明确的一次性串行授权下，Finnhub、EIA Open Data 和 SEC EDGAR 各完成
  一次 bounded smoke，脱敏结构结果均为 PASS。
- 四个 smoke 证据均未保存 key/token/contact email、完整 response、raw payload 或真实
  title/body/URL/quote/EIA/filing value；NewsAPI.ai 与 GDELT 未执行。
- 纠正 SPEC-0004 provider 决策流程：平台选择由 ChatGPT / 用户确认，Primary Provider 为
  NewsAPI.ai / Event Registry（pending credentials），Codex 不负责重新评估或改选。
- 记录 Marketaux 为 Secondary Financial News candidate、Finnhub 为 Market Validation
  candidate，SEC EDGAR / EIA / Company IR / Official RSS 为 Official Evidence Layer。
- GDELT 在最终 provider decision 前曾被探索；现已被 supersede 为 `runtime blocked /
  future evaluation only`，不再是 SPEC-0004 primary pilot，且不得继续 smoke。
- 本轮无代码、迁移、依赖、API request、adapter 或 collection；implementation still not
  started。
- 补充平台执行顺序和统一门禁：注册并保存凭证 → 单平台 bounded smoke → 用户/ChatGPT
  Review → 确认字段/额度/许可/保存边界 → 对应 SPEC → Adapter implementation → 测试合并。
- 定义 bounded smoke 必录字段与 PASS 标准；任何关键 schema、quota、license、retention
  或安全门禁未满足时不得开始实现。
- 记录唯一一次 GDELT corrected smoke：超过 60 分钟冷却、使用 `timespan=15min` 和
  `maxrecords=1` 后仍返回 HTTP 429；DOC 2.0 pilot 标记为 `runtime blocked`。
- corrected smoke 未产生有效 JSON/schema evidence；未保存完整 response、真实 title/body/
  URL 值，未访问 source page，未写代码、迁移、依赖、adapter 或运行 collection。
- 增加 GDELT failure analysis before further smoke：官方参数审计确认 `timespan=15m` 不是
  15 分钟语法，记录 DOC rate-limit 官方边界、SSL timeout 未确认边界和下一次单请求 gate。
- 当时的 failure-analysis 轮次无代码、迁移、依赖、API request、adapter 或 collection；
  implementation 未开始。
- 记录 GDELT Project DOC 2.0 bounded smoke verification：两次极小请求尝试分别观察到 HTTP
  429 和 SSL connection timeout；成功响应 schema 等仍保持 Blocked。
- 本轮无代码、迁移、依赖、adapter、adapter key 或 collection；未访问 source page，未保存
  完整 response、真实 GDELT 数据或新闻正文，implementation still not started。
- 细化 SPEC-0004 GDELT preimplementation verification：选择 DOC 2.0 ArticleList JSON
  endpoint family，记录时间窗、结果上限、开放数据引用要求、最小留存和 fail-closed 边界。
- 明确区分 GDELT Project DOC 2.0 与 GDELT Cloud，防止实现阶段混用 API 合同、认证、分页、
  rate limit、许可或再分发条款。
- 明确数值 rate limit、timeout/retry、真实响应字段和 endpoint smoke evidence 仍为
  Pending/Blocked；本轮无代码、迁移、依赖、adapter、真实 collection 或 GDELT 数据保存。
- 激活 `SPEC-0004 — First Approved Polling Source Pilot` 进入文档审核。
- 创建 provider-neutral Polling Source Pilot 规格草案，并记录当时用户选择的 GDELT
  candidate；该历史选择现已被最终 provider decision supersede。
- 细化 GDELT Source Contract；未真实核验的 endpoint、认证、许可、terms/robots、rate limit、
  timeout 和验证证据保持 `Pending verification before implementation`。
- 明确本次没有代码、迁移、schema、依赖或真实来源变更。
- Foundation v2.1-FROZEN 继续生效；SPEC-0004 Active 只表示 Docs Review，不授权实现。

## Unreleased — Architecture Documentation Alignment

### Documentation

- 将 SPEC-0003 标记为已完成，并将仓库状态调整为暂无 Active SPEC。
- 记录下一 Foundation 版本的候选架构方向：供应商中立混合采集、统一接入网关、
  可替换事件总线、逻辑 Unified News Record、事件中心化研究体验、三层 AI 研究链路与
  市场数据验证。
- 补充候选来源目录、采集模式、恢复与时间语义、许可元数据、研究建议语义及未来 SPEC
  拆分建议。
- 明确上述内容仅为 Proposed Decisions；Foundation v2.1-FROZEN 仍然有效，未激活新
  SPEC，未进行代码、迁移或 schema 变更。

## SPEC-0003 Implementation — 2026-07-29

### Fixed

- 使用 Redis `SET NX EX` dispatch marker，防止重复 dispatcher、Beat replay 或进程重启对同一 target/slot 重复 enqueue。
- worker 在创建 CollectionRun 前验证 account 存在、归属与 enabled，并禁止有账号来源使用 source-level target。
- account-level success 不再依据旧历史 run 推断“本轮全部成功”，避免掩盖其他账号失败。

### Added

- 来源无关的 collection contract、仅含 fake adapter 的 registry，以及确定性 dispatcher。
- Celery collection tasks、Redis owner-token lease、CollectionRun 生命周期、RawItem/cursor 原子 checkpoint、retry/error classification、source health 和 stale run recovery。
- 单元、PostgreSQL 16、Redis/Celery 与故障注入测试。

### Changed

- 增加无秘密 collection 配置模板和 Celery Beat schedule。
- CI 增加 Redis 7 service。

### Scope

- 无 schema 变化、无 Alembic revision、未修改 ORM 模型或 Foundation。
- 未接入真实来源、Normalization/Dedup/Outbox、Telegram、AI 或未来阶段实体。

## SPEC-0003 Collection Framework, Scheduler, Cursor and Retry — 2026-07-29

### Added

- 创建 `spec/SPEC-0003.md`，严格定义下一阶段的 adapter contract、调度框架、CollectionRun 生命周期、cursor checkpoint、retry、错误分类、测试和验收要求。

### Changed

- 将 SPEC-0002 标记为 Completed；
- 将 SPEC-0003 设为唯一 Active SPEC，并更新 AI Context 入口。

### Scope

- 本次仅创建和激活规格文档，未开始 SPEC-0003 实现；
- 未新增代码、迁移、数据库表、真实来源、Telegram、Normalization/Dedup/Outbox 行为、AI 或后续阶段实体；
- 未修改 Foundation v2.1-FROZEN；实现须在 SPEC-0003 文档经用户或 Reviewer 审核 PASS 后开始。

## SPEC-0002 Implementation — 2026-07-29

### Added

- 九个 Phase 1 SQLAlchemy ORM 模型：Source、SourceAccount、CollectionCursor、CollectionRun、RawItem、ContentItem、Notification、OutboxMessage、AuditLog；
- PostgreSQL 16 原生 UUID、timestamptz、JSONB、enum、检查约束、外键、唯一索引和查询索引；
- Alembic revision `0002_create_phase1_data_model`，支持 `upgrade → downgrade -1 → upgrade`；
- PostgreSQL 集成测试、ORM/迁移一致性检查以及 schema allowlist/denylist 测试；
- CI PostgreSQL 16 service 和 migration upgrade 步骤。

### Changed

- Alembic metadata 现在加载九个 Phase 1 模型，并保留 SPEC-0001 的 `system_metadata` 基础设施表；
- `raw_items` 通过非空 `collection_run_id` 追溯采集运行；
- `outbox_messages` 使用非空且唯一的 `idempotency_key` 防止 retry 重复发布记录。

### Scope

- 未实现 API、CLI、service、repository、adapter、collector、scheduler、真实来源、Telegram、标准化、Notification 策略或 Outbox 发布；
- 未创建 Event、Evidence、Analysis、Portfolio、Holding、Investment Plan、Candidate Rule 或其他未来阶段实体；
- 未修改 Foundation v2.1-FROZEN。

## SPEC-0002 Source Registry and Phase 1 Data Model — 2026-07-29

### Added

- 创建 `spec/SPEC-0002.md`，严格定义下一阶段的实现范围、非范围、九个 Phase 1 数据实体、字段、约束、迁移、测试与验收要求。

### Changed

- 将 SPEC-0001 标记为 Completed；
- 将 SPEC-0002 设为唯一 Active SPEC，并更新 AI Context 入口。

### Scope

- 本次仅创建和激活规格文档，未开始 SPEC-0002 实现；
- 未新增模型、迁移、业务代码、真实来源、Telegram、AI 或后续阶段实体；
- 未修改 Foundation v2.1-FROZEN；实现须在 SPEC-0002 文档经用户或 Reviewer 审核 PASS 后开始。

## SPEC-0001 Project Bootstrap — 2026-07-29

### Fixed

- `.env.example` 改为宿主机 `localhost` 配置，Compose 暴露 PostgreSQL/Redis 端口并保留容器内部服务名；
- `session_scope` 改为异步 context manager，并增加生命周期测试；
- `DATABASE_URL` 仅允许 SPEC 固定的 PostgreSQL asyncpg URL；
- 完成 Compose 健康、Alembic 往返与 Celery health task 的真实运行时验收。

### Added

- Python 3.12 `src/` 项目骨架、FastAPI 应用与 live/ready 健康接口；
- Pydantic Settings、结构化日志和关联 ID；
- SQLAlchemy 2.x、Alembic 及仅含 `system_metadata` 的首条基础迁移；
- Redis 7、Celery 5 worker、Celery Beat 与无副作用健康任务；
- uv 锁文件、Dockerfile、Docker Compose、pytest、Ruff、mypy 与 GitHub Actions CI；
- 安全配置模板、启动/迁移/测试/排错文档和交付报告。

### Scope

- 未修改 Foundation v2.1-FROZEN；
- 未引入任何 Phase 1 业务实体、真实来源、Telegram、AI 或后续阶段实体。

## Foundation v2.1-FROZEN — 2026-07-28

### Frozen

- 单用户、美股/ETF/Crypto、Broad Scan、Controlled Push、四阶段边界、自动交易禁止和 Phase 1 技术基线。

### Changed

- Phase 1 收敛为采集、原始留痕、确定性标准化、确定性去重、存储、Outbox 和 Telegram 推送；
- Phase 1 改为 Content First，Phase 2 起 Event First；
- AI/Event/Evidence 移至 Phase 2；Portfolio/Investment Plan 移至 Phase 3；
- P0–P4 改为 Phase 1 可执行的确定性规则；
- 管理 Bot 收缩为运维管理；
- Source Catalog 增加实现契约；
- uv 固定为依赖与锁文件工具。

### Added

- FOUNDATION_FROZEN.md；
- docs/PHASE1_ACCEPTANCE.md；
- docs/FREEZE_REVIEW.md；
- spec/SPEC_INDEX.md；
- scripts/validate-foundation.py；
- MANIFEST.sha256。

## Foundation v2.0 — 2026-07-28

### Added

- 单用户、美股与 Crypto 的正式项目边界；
- Collection、Analysis、Notification、Portfolio 四个 Scope；
- Broad Scan 与 Controlled Push；
- P0–P4 通知定义；
- Content Item、Event、Evidence、Investment Plan 等统一术语；
- Source Catalog 与稳定账号 ID 验证要求；
- 核心决策记录；
- 可审计的 SPEC 和 Delivery Report 模板；
- Phase 1 可执行技术基线；
- 安全打包与秘密扫描脚本。

### Changed

- Phase 3 改为持仓影响、组合风险和投资计划复核；
- 删除 AI 替用户选择买卖、加减仓等交易动作的权限；
- 长期优化改为显式反馈和用户确认；
- Telegram 被定义为主要入口而非整个服务端后台；
- 原始 Content Item 与 Event 分层保存；
- 项目信息源只保存当前有效知识，Git 保存历史。

### Removed

- 多用户、Workspace、团队和 SaaS；
- 隐式点击、打开、忽略行为学习；
- 自动缩小信息覆盖；
- 自动修改投资计划；
- 自动交易和确定性投资指令；
- 以未授权方式获取或保存付费正文。
