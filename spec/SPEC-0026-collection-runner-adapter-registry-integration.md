# SPEC-0026 — Collection Runner Adapter Registry Integration

Status：Completed — Implementation Review approved

Phase：Phase 1 — Collection Runner Adapter Registry Integration

Foundation：v2.1-FROZEN（unchanged）

Depends on：SPEC-0025（Completed — Implementation Review approved）

## 1. 目标

实现现有 `CollectionRunner` 与 `ProviderAdapterRegistry` 的最小受控边界，使 runner 仅通过注入的
mocked ProviderAdapter/transport 获取 `ProviderFetchResult`，把其中 `raw_items` 持久化为
`RawItem`，并且只在 RawItem 持久化成功后于同一事务推进 cursor/checkpoint。

本 PR 包含 Python implementation code，不是 docs-only。

## 2. 实现范围

- 新增 provider-to-collection bridge，将 `ProviderFetchRequest/Result` 映射到既有
  `FetchRequest/FetchBatch`。
- 为 `CollectionRunner` 增加显式、可选的 `ProviderAdapterRegistry` 与 `ProviderTransport` 注入点。
- 保留现有 fake `AdapterRegistry` 和 regression 行为。
- 只允许显式注册的 mocked Marketaux scaffold；unknown key 无 fallback。
- 复用 runner 已有 RawItem/cursor 同事务 persistence boundary。
- 将 provider safe error 映射为固定、redacted collection error。
- 增加 mock-only 与 PostgreSQL integration tests。

## 3. 调用与持久化边界

`CollectionTarget.access_method` 必须同时匹配数据库 `Source.access_method` 与显式 provider registry
key。Source 必须 enabled，且 `authorization_status` 只能是 `authorized` 或 `implemented`；account
存在性、所属 Source 与 enabled gate 沿用既有 fail-closed 校验。

Provider Adapter 只执行 fetch contract，不持有 session，不写 DB。runner bridge 将安全结果转换为
`FetchBatch`；`CollectionRunner._persist_checkpoint` 才能创建 `RawItem`。RawItem 与 cursor update 在
同一数据库事务中：任一 RawItem insert 失败则整个 checkpoint rollback，不推进 cursor。

## 4. Cursor contract

- provider cursor type 固定为 `provider_cursor_v1`；
- cursor 使用 adapter 已生成的 opaque deterministic JSON；
- bridge 只验证 candidate 可解析且严格大于当前 `(published_at, provider_item_id)`；
- sanitized metadata 中可解析的 timezone-aware `published_at` 形成 watermark；
- persistence 成功前不得写 cursor；
- historical backfill、scheduler-driven cursor creation 不在本 SPEC。

## 5. Safe error mapping

- provider config invalid → `COLLECTION_CONFIG_INVALID`；
- provider contract invalid → `COLLECTION_CONTRACT_INVALID`；
- rate limited → `COLLECTION_RATE_LIMITED`，只传 bounded Retry-After；
- timeout → `COLLECTION_TIMEOUT`；
- retryable upstream → `COLLECTION_UPSTREAM_RETRYABLE`；
- non-retryable upstream → safe contract failure。

错误不得包含 request/response、title/body/URL、secret、credential、SQL params 或 raw payload。
safe error 不创建 RawItem；重试/失败沿用现有 CollectionRun lifecycle。

## 6. 严格非范围

- 不请求真实 API，不读取 `.env`，不执行 `provider_capture.py --execute`。
- 不读取或提交 raw capture/`local_evaluation/`。
- 不实现 real Marketaux/Finnhub/EIA/SEC adapter。
- 不调用 `EvidenceWriteService`，不写 `evidence_items`。
- 不实现 RawItem → `provider_mappings.py` → EvidenceWriteService orchestration。
- 不实现 scheduler 或更改 dispatcher/Beat wiring。
- 不实现 formal normalization、dedup、clustering、Event 或 AI。
- 不实现 Telegram、investment recommendation、Portfolio/Holding 或交易动作。
- 不启动 SPEC-0022；SPEC-0005 X Source 范围不变。

## 7. 测试要求

- runner 通过 provider registry 找到 mocked Marketaux scaffold；
- unknown key fail closed，无 fallback；
- disabled/unauthorized Source 或 account 不调用 adapter；
- mocked `RawItemEnvelope` 成功写为 RawItem；
- RawItem persistence 成功后才推进 cursor；失败时 RawItem/cursor 同时 rollback；
- adapter safe error、rate limit、timeout 映射为 safe CollectionRun error；
- Adapter 不写 DB，DB write 只发生在 runner persistence 层；
- 不调用 EvidenceWriteService，不写 evidence_items；
- 不读取 `.env`/capture/local files，不访问真实网络；
- 不引入 Event/AI/Telegram/recommendation 依赖；
- 保留 existing fake adapter regression tests。

## 8. 验收标准

- [x] Python provider-to-collection bridge 已实现。
- [x] runner 支持显式 provider registry/mock transport injection。
- [x] mocked Marketaux RawItemEnvelope 可持久化为 RawItem。
- [x] cursor 仅在 RawItem persistence 成功后推进。
- [x] unknown/unauthorized/safe error fail closed。
- [x] fake adapter regression 保持通过。
- [x] 无真实 API、`.env`、capture、Evidence、scheduler、dedup/Event/AI。
- [x] Reviewer/CI/完整验证与安全 review package PASS。

## 9. Verification Evidence

以本 PR source diff、mock-only tests、PostgreSQL integration tests、完整 regression、Foundation
validator 与安全 review package 为证。未请求 API、读取 `.env`/capture/`local_evaluation/`，未写
evidence_items，未启动 SPEC-0022。

## 10. Review History

| Round | Result | Evidence | Resolution |
|---|---|---|---|
| 1 | IN REVIEW | 本 implementation PR、CI 与 review package | 等待用户/ChatGPT Implementation Review |
| 2 | PASS | PR #26、CI 与 ChatGPT review | Collection runner mocked adapter integration approved and merged |

PR #26 已完成 CollectionRunner → ProviderAdapterRegistry mocked integration、
`ProviderCollectionAdapter` bridge、`RawItemEnvelope` → RawItem persistence、成功持久化后的 cursor
checkpoint，以及 provider safe error → CollectionRun safe error mapping。该 PR 未实现 RawItem →
Evidence orchestration、真实 API adapter、scheduler、dedup/Event/AI 或 Telegram。

## 11. 后续门禁

本 SPEC PASS 只批准 mocked collection integration。真实 adapter、credential wiring、scheduler、
RawItem → Evidence orchestration、formal normalization/dedup/Event/AI 必须由新的独立 SPEC 授权。
