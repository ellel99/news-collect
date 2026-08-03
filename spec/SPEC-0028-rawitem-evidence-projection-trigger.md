# SPEC-0028 — RawItem Evidence Projection Store and Pipeline Trigger

Status：Completed — Implementation Review approved

Phase：Phase 1 — RawItem Evidence Projection Store and Explicit Trigger

Foundation：v2.1-FROZEN（unchanged）

Depends on：SPEC-0027（Completed — Implementation Review approved）

## 1. 目标

实现最小 content-free projection store/reader 与 explicit pipeline trigger，使已持久化的 mocked
Marketaux RawItem 能经安全 projection 进入 `EvidencePipelineService`，最终只通过
`EvidenceWriteService` 写入 evidence_items。

本 PR 写 Python implementation code，不是 docs-only；使用 synthetic/mock data only。

## 2. Projection store

`RawItemEvidenceProjection` 只包含：

- raw_item/source/source_account provenance UUID；
- provider（本 SPEC 固定 `marketaux`）；
- content-free sanitized projection；
- timezone-aware observed time；
- content-free correlation ID。

Sanitized projection 沿用 SPEC-0027 exact allowlist：provider item ID、published time、field names、
title/description/snippet/source URL presence booleans、payload hash 和 opaque internal/capture/local-ref
reference。`InMemoryEvidenceProjectionStore` 只用于显式 synthetic/mock flow；duplicate key、unknown
provider、malformed/unsafe projection 均 fail closed。

Store 不保存 title/body/URL/snippet/description 实际值、raw payload、provider response、secret-bearing
URL/header/value，也不读文件、`.env` 或 Provider API。

## 3. RawItem reader

`SqlAlchemyRawItemProjectionReader` 只从既有 RawItem 读取：ID、source/account provenance、external
ID、fetched time、payload hash 与 payload location。它不新增表/字段，不读取 raw provider response，
不写 DB。

## 4. Explicit trigger

`RawItemEvidencePipelineTrigger.trigger(raw_item_id)`：

1. 通过 safe reader 查找 RawItem；
2. 从 projection store 查找相同 RawItem ID；
3. 核对 source/account、provider item ID/external ID、payload hash 与 opaque reference；
4. 构造 `EvidencePipelineRequest`；
5. 调用 `EvidencePipelineService`；
6. 返回只含 UUID、status 与固定 safe error 的 trigger outcome。

missing RawItem、missing projection 或任何 mismatch 均在 Pipeline 前 fail closed。Trigger 不调用
collection runner/scheduler，不直接写 evidence_items，也不绕过 EvidencePipelineService/
EvidenceWriteService。

## 5. DB/schema 边界

- 不新增 migration、table、column、index、constraint 或 ORM model；
- projection store 为显式内存/test scaffold，不伪装为 durable production store；
- RawItem reader 只读既有 schema；
- evidence persistence 仍完全由已审核的 EvidenceWriteService 负责。

## 6. 严格非范围

- 不请求真实 API，不读取 `.env`，不执行 `provider_capture.py --execute`。
- 不读取或提交 raw capture/`local_evaluation/`。
- 不实现 real Marketaux/Finnhub/EIA/SEC adapter。
- 不调用 collection runner，不实现 scheduler 或自动调度。
- 不支持 Finnhub/EIA/SEC projection trigger。
- 不实现 formal normalization、dedup、clustering、Event 或 AI。
- 不实现 Telegram、investment recommendation、Portfolio/Holding 或交易动作。
- 不启动 SPEC-0022；SPEC-0005 X Source 范围不变。

## 7. 测试要求

- persisted Marketaux RawItem + safe projection → trigger → evidence_items success；
- trigger 只经 EvidencePipelineService/EvidenceWriteService persistence；
- missing RawItem/projection 与 provenance/hash/reference mismatch fail closed；
- malformed/content-bearing/secret projection 不进入 store/outcome/error；
- duplicate trigger 返回 duplicate，不重复 evidence_items；
- source audit 禁止 requests/httpx/provider_capture/local_evaluation、collection runner/scheduler、
  Event/AI/Telegram/recommendation dependency；
- PostgreSQL semantics，不用 SQLite 替代关键 lookup/provenance/idempotency；
- existing collection/evidence tests 保持通过。

## 8. 验收标准

- [x] Python projection store、RawItem safe reader 与 explicit trigger 已实现。
- [x] Marketaux synthetic projection 可触发 Evidence Pipeline。
- [x] RawItem/projection identity 与 provenance pre-check 已实现。
- [x] missing/malformed/secret/mismatch/duplicate tests 已覆盖。
- [x] 无 migration/ORM/schema change。
- [x] 无真实 API、`.env`、capture、real adapter、scheduler、dedup/Event/AI/Telegram。
- [x] SPEC-0022 未启动，Foundation v2.1-FROZEN 未修改。
- [x] Reviewer/CI/完整验证与安全 review package PASS。

## 9. Verification Evidence

以本 PR source diff、synthetic-only PostgreSQL tests、existing collection/evidence regression、source
audit、完整 test suite、Foundation validator 与安全 review package 为证。未请求 API、读取 `.env`/
capture/`local_evaluation/`，未修改 schema，未启动 SPEC-0022。

## 10. Review History

| Round | Result | Evidence | Resolution |
|---|---|---|---|
| 1 | IN REVIEW | 本 implementation PR、CI 与 review package | 等待用户/ChatGPT Implementation Review |
| 2 | PASS | PR #28；projection store/reader/trigger、PostgreSQL tests 与安全边界通过审核 | Completed — Implementation Review approved |

PR #28 已交付 projection store、RawItem safe reader、explicit pipeline trigger、provenance/identity
核对以及 PostgreSQL tests。它未实现 collection-to-evidence end-to-end orchestration、真实 Adapter、
scheduler、formal normalization、dedup、Event、AI 或 Telegram，也未启动 SPEC-0022。

## 11. 后续门禁

本 SPEC PASS 只批准 Marketaux synthetic projection explicit trigger。durable projection storage、真实
adapter/runtime payload、其他 Provider、scheduler、formal normalization/dedup/Event/AI/Telegram 必须
由独立 SPEC 授权。
