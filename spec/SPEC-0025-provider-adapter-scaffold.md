# SPEC-0025 — Provider Adapter Scaffold Implementation

Status：Completed — Implementation Review approved

Phase：Phase 1 — Provider Adapter Scaffold Implementation

Foundation：v2.1-FROZEN（unchanged）

Depends on：SPEC-0024（Completed — Docs Review approved）

## 1. 目标

实现 Provider Adapter 的最小 Python scaffold：纯合同、fail-closed registry、显式 mocked transport 与
Marketaux mocked adapter，使调用方能从 registry 解析 scaffold 并生成安全 `RawItemEnvelope`。

本 PR 明确包含 Python implementation code，不是 docs-only；但只实现 scaffold，不接真实网络、
collection runner、数据库或后续 evidence pipeline。

## 2. 实现范围

- `providers/contracts.py`：request/result、safe error、transport/adapter Protocol。
- `providers/registry.py`：`ProviderAdapterRegistry`，unknown key fail closed，无 fallback。
- `providers/transport.py`：队列式 `MockProviderTransport`，仅供 synthetic tests 注入。
- `providers/marketaux.py`：Marketaux response-shape scaffold，只通过注入 transport fetch。
- 更新 provider package import surface。
- mock-only 单元测试与 source audit。

不修改 collection runner、Celery task、scheduler、DB、migration 或 ORM。

## 3. ProviderFetchRequest

字段：

- `source_id`；
- optional `source_account_id`；
- opaque/versioned cursor string；
- immutable non-secret config；
- bounded limit；
- timezone-aware deadline；
- correlation ID。

request 不包含 credential value。Marketaux scaffold config 只允许 `query` 和正数
`timeout_seconds`；未知/secret-bearing config fail closed，不进入 transport。

## 4. ProviderFetchResult

字段：

- `raw_items: tuple[RawItemEnvelope, ...]`；
- 与 raw item 对齐的 content-free `sanitized_metadata`；
- deterministic `next_cursor`；
- `has_more`；
- `safe_errors`；
- provider 与 contract version。

Result 强制 raw item/metadata 数量一致。输出不含 title/body/description/snippet/URL value、raw response、
secret、Authorization 或 DB entity。

## 5. Registry

- `register(provider_key, adapter)` 要求 key 与 adapter 声明一致；
- duplicate registration fail closed；
- `get(provider_key)` unknown 时抛固定 `provider_adapter_unregistered`，不回显未知 key；
- 不 fallback 到 fake、网页、其他 Provider 或 preflight client；
- 本 SPEC 只注册/测试显式构造的 Marketaux scaffold，不修改现有 collection fake registry。

## 6. Transport boundary

`ProviderTransport` 只定义 async `send`。`MockProviderTransport` 接收预置 synthetic response/exception，
记录安全 call contract，不创建任何 socket/client。

`ProviderTransportRequest` 只包含 provider、operation、non-secret params、timeout。key 名或值包含
api-key/token/Authorization 等 marker 时构造即拒绝。不得把 credential 作为 CLI、request param、
header、URL、日志或 result 传递。

## 7. Marketaux scaffold

实现范围：

- mocked response 的 `data` list shape validation；
- stable item ID 优先必填 `uuid`；
- 必填 `published_at`；
- limit 1–3，超出在 transport 前 fail closed；响应超过 limit 时只输出 bounded records 并设置
  `has_more`；
- next cursor 为 `published_at + provider_item_id` 的 deterministic JSON；非法 cursor fail closed；
- sanitized metadata 只含 ID/time、field names 和 title/description/snippet/source URL presence；
- payload hash 基于 sanitized content-free projection；payload location 为 internal opaque reference；
- retention 固定 `metadata_only`；
- response echo secret/secret field name 不进入 metadata/hash/result；identity/time 含 secret marker 时整项
  fail closed；
- 429、timeout、4xx、5xx 和 malformed response 映射固定 safe error。

这不是 Marketaux real API adapter；不构造真实 endpoint、credential、HTTP client 或 production retry
loop，也不下载 article page/fulltext。

## 8. Error contract

Safe codes：

- `provider_config_invalid`；
- `provider_contract_invalid`；
- `provider_rate_limited`；
- `provider_timeout`；
- `provider_upstream_error`。

Error 只含固定 safe message、retryable flag 和 bounded numeric Retry-After。不得包含 request/response、
title/body/URL、secret、exception raw text 或 SQL parameter。

## 9. 严格非范围

- 不请求真实 API，不读取 `.env`，不执行 `provider_capture.py --execute`。
- 不读取或提交 raw capture/`local_evaluation/`。
- 不实现 Marketaux/Finnhub/EIA/SEC real API adapter。
- 不修改或接入 collection runner、scheduler、Celery task 或现有 fake adapter registry。
- Adapter 不调用 `EvidenceWriteService`，不写 `evidence_items`，不直接写 DB。
- 不实现 formal normalization、canonicalization、dedup、clustering、Event 或 AI。
- 不实现 Telegram、investment recommendation、Portfolio/Holding 或交易动作。
- 不下载网页正文、full article、SEC filing body 或附件。
- 不启动 SPEC-0022；SPEC-0005 X Source 范围不变。

## 10. 测试要求

- registry register/get 与 unknown fail-closed/no fallback；
- mocked Marketaux response 生成安全 `RawItemEnvelope`；
- title/description/snippet/URL value、secret-bearing field/value 不进入 result；
- provider echo secret 被 allowlist/drop 策略隔离；
- record limit 与 deterministic cursor；
- malformed response、invalid cursor/config fail before transport；
- 429/Retry-After、timeout、4xx/5xx safe classification；
- transport request 拒绝 secret field/value；
- source audit 无真实 network、EvidenceWriteService、DB、capture/local files、collection runner、
  scheduler、Event/AI/Telegram/investment dependencies；
- test suite 不访问外部网络。

## 11. 验收标准

- [x] Python scaffold contracts/registry/mock transport 已实现。
- [x] Marketaux mocked adapter scaffold 输出安全 RawItemEnvelope。
- [x] limit/cursor/shape/secret/error tests fail closed。
- [x] Adapter 无 EvidenceWriteService、evidence_items 或 DB write dependency。
- [x] 无真实 API、`.env`、capture、collection/scheduler、formal normalization/dedup/Event/AI。
- [x] SPEC-0022 未启动，Foundation v2.1-FROZEN 未修改。
- [x] Reviewer/CI/完整验证与安全 review package PASS。

## 12. Verification Evidence

以本 PR Python/source diff、22 个 focused mock-only tests、完整 regression、source audit、Foundation
validator 与安全 review package 为证。未请求 API、读取 `.env`/capture/`local_evaluation/` 或写 DB。

## 13. Review History

| Round | Result | Evidence | Resolution |
|---|---|---|---|
| 1 | IN REVIEW | 本 implementation PR、CI 与 review package | 等待用户/ChatGPT Implementation Review |
| 2 | PASS | PR #25、CI 与 ChatGPT review | Provider Adapter scaffold implementation approved and merged |

PR #25 已完成 `ProviderAdapter` Protocol、`ProviderFetchRequest`/`ProviderFetchResult`、
`ProviderTransport`/`MockProviderTransport`、fail-closed `ProviderAdapterRegistry`、Marketaux mocked
scaffold、`RawItemEnvelope` mock output 与 source audit tests。该 PR 未实现 collection runner
integration、真实 API adapter、scheduler、RawItem 到 Evidence orchestration、dedup/Event/AI 或
Telegram。

## 14. 后续门禁

Implementation Review PASS 只批准 scaffold。真实 Provider Adapter、credential wiring、collection
runner integration、scheduler 或 API request 必须由新的独立 SPEC 明确授权；不得自动继续，也不得启动
SPEC-0022。
