# SPEC-0027 — RawItem to Evidence Pipeline Orchestration

Status：Active — Implementation Review

Phase：Phase 1 — RawItem to Evidence Pipeline Orchestration

Foundation：v2.1-FROZEN（unchanged）

Depends on：SPEC-0023 implementation、SPEC-0026（Completed — Implementation Review approved）

## 1. 目标

实现最小、受控、mock-only 的 RawItem → sanitized provider projection →
`provider_mappings.py` → `CommonEvidenceEnvelope` → `EvidenceWriteService` orchestration。

本 PR 写 Python implementation code，不是 docs-only；只支持 Marketaux synthetic/content-free
projection，不接真实 Provider runtime。

## 2. 输入合同

`EvidencePipelineRequest` 仅包含：

- `raw_item_id`、`source_id`、optional `source_account_id`；
- provider（本 SPEC 只能为 `marketaux`）；
- sanitized projection；
- timezone-aware `observed_at`；
- content-free correlation ID。

Marketaux projection 必须精确包含：

- `provider_item_id`；
- `published_at`；
- `field_names`；
- `has_title`、`has_description`、`has_snippet`、`has_source_url`；
- 64 位 lowercase SHA-256 `payload_hash`；
- internal/capture/local-ref opaque `payload_reference`。

不允许 actual title/body/URL/snippet/description、raw payload、secret-bearing field/value、unknown
field 或 provider SDK object。

## 3. Orchestration boundary

`EvidencePipelineService`：

1. fail-closed 验证 provider 与 projection allowlist；
2. 仅把 ID/time 与 presence booleans 转换为现有 Marketaux mapper 的 content-free input；
3. 调用 `map_marketaux_news_to_evidence`；
4. 使用 projection 的 payload hash 与 opaque internal reference 建立 RawItem linkage；
5. 调用 `validate_evidence_envelope`；
6. 构造 `EvidenceWriteRequest`；
7. 只通过注入的 `EvidenceWriteService.write_one` persistence boundary 写入；
8. 将 write status/error 映射为 content-safe pipeline outcome。

Orchestration 模块不导入 ORM、SQLAlchemy 或 `EvidenceItem`，不得绕过 Write Service 直接写 DB。
RawItem 存在性、Source/account provenance 与 provider-scoped duplicate/conflict 继续由已审核的
EvidenceWriteService 强制。

## 4. Outcome 与错误

状态：`written`、`duplicate`、`invalid`、`failed`、`skipped`。

Outcome 只含 status、opaque RawItem/evidence UUID、provider、provider item hash 与固定 safe errors。
不含 provider item raw ID、title/body/URL/snippet/description、raw payload、secret、SQL params 或实际
Provider value。

- unknown/unsupported provider → `provider_unsupported` / skipped；
- malformed/unsafe projection → `projection_invalid` / invalid；
- mapping/contract failure → fixed safe invalid error；
- missing RawItem → Write Service `reference_not_found`；
- provenance mismatch → Write Service `provenance_mismatch`；
- duplicate/existing → duplicate outcome，不重复插入。

## 5. 严格非范围

- 不请求真实 API，不读取 `.env`，不执行 `provider_capture.py --execute`。
- 不读取或提交 raw capture/`local_evaluation/`。
- 不实现 real Marketaux/Finnhub/EIA/SEC adapter。
- 不调用 collection runner，不实现 scheduler。
- 不支持 Finnhub、EIA 或 SEC evidence orchestration。
- 不实现 formal normalization、dedup、clustering、Event 或 AI。
- 不实现 Telegram、investment recommendation、Portfolio/Holding 或交易动作。
- 不修改 migration、ORM 或 DB schema。
- 不启动 SPEC-0022；SPEC-0005 X Source 范围不变。

## 6. 测试要求

- synthetic Marketaux projection → envelope → evidence_items 成功；
- persistence 必须通过 EvidenceWriteService，不直接写 DB；
- duplicate 返回 duplicate/existing；
- malformed/unsafe projection fail closed，不写 evidence_items；
- unknown provider fail closed；
- missing RawItem 与 provenance mismatch fail closed；
- secret/raw content 不进入 outcome/error；
- source audit 禁止 requests/httpx/provider_capture/local_evaluation、collection runner/scheduler、
  ORM/EvidenceItem、Event/AI/Telegram/recommendation dependency；
- PostgreSQL semantics，不用 SQLite 替代关键 persistence/provenance/idempotency 行为；
- existing EvidenceWriteService tests 保持通过。

## 7. 验收标准

- [x] Python EvidencePipeline request/outcome/service 已实现。
- [x] Marketaux synthetic sanitized projection mapper dispatch 已实现。
- [x] 只通过 EvidenceWriteService 写 evidence_items。
- [x] success/duplicate/malformed/unknown/missing/provenance/secret-safe tests 已覆盖。
- [x] 无真实 API、`.env`、capture、real adapter、scheduler、dedup/Event/AI/Telegram。
- [x] SPEC-0022 未启动，Foundation v2.1-FROZEN 未修改。
- [ ] Reviewer/CI/完整验证与安全 review package PASS。

## 8. Verification Evidence

以本 PR source diff、synthetic-only PostgreSQL tests、existing Write Service regression、source audit、
完整 test suite、Foundation validator 与安全 review package 为证。未请求 API、读取 `.env`/capture/
`local_evaluation/`，未修改 schema，未启动 SPEC-0022。

## 9. Review History

| Round | Result | Evidence | Resolution |
|---|---|---|---|
| 1 | IN REVIEW | 本 implementation PR、CI 与 review package | 等待用户/ChatGPT Implementation Review |

## 10. 后续门禁

本 SPEC PASS 只批准 Marketaux synthetic projection orchestration。真实 adapter/runtime payload、其他
Provider、scheduler、formal normalization/dedup/Event/AI/Telegram 必须由独立 SPEC 授权。
