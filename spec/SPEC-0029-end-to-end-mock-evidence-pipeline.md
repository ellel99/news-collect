# SPEC-0029 — End-to-End Mock Collection Evidence Pipeline

Status：Active — Implementation Review

Phase：Phase 1 — End-to-End Mock Collection Evidence Pipeline

Foundation：v2.1-FROZEN（unchanged）

Depends on：SPEC-0026、SPEC-0027、SPEC-0028（Completed）

## 1. 目标

实现一条明确、受控、mock-only 的端到端执行路径：

```text
mocked Marketaux transport
→ MarketauxAdapter scaffold
→ ProviderCollectionAdapter
→ CollectionRunner
→ RawItem persistence / cursor checkpoint
→ content-free projection sidecar binding
→ RawItemEvidencePipelineTrigger
→ EvidencePipelineService
→ EvidenceWriteService
→ evidence_items
```

本 PR 必须写 Python implementation code，不是 docs-only。只使用 mocked Marketaux response 与
PostgreSQL/Redis test runtime；不得访问真实 Provider。

## 2. 端到端边界

- provider bridge 可向显式 observer 提交 `ProviderFetchResult` 的 content-free
  `sanitized_metadata` 与 RawItemEnvelope identity/hash/internal reference；observer 不持久化 DB。
- CollectionRunner 仍是 RawItem 与 cursor 的唯一 persistence boundary；Adapter 不直接写 DB。
- 只有 CollectionRun 成功、RawItem 已安全持久化后，显式 orchestrator 才查询本 run 的 RawItem。
- sidecar 必须按 external ID、payload hash 与 opaque reference 绑定 projection；missing/invalid/mismatch
  均 fail closed，不触发 evidence write。
- trigger 必须继续通过 EvidencePipelineService 与 EvidenceWriteService 写 evidence_items；orchestrator
  不得绕过既有 write path。
- 重放同一成功 run 使用 provider-scoped idempotency，返回 duplicate/existing，不重复 evidence row。

## 3. 安全输出

Orchestrator outcome 只允许包含 run/raw/evidence UUID、计数、枚举状态与固定 safe error code/message。
不得包含 title、body、URL、snippet、description、provider raw value、secret、Authorization、SQL 参数或
完整 payload。sidecar 仅存 content-free field names/presence flags、provider ID、timestamp、hash 与 opaque
internal reference。

## 4. 失败行为

- unauthorized/disabled Source 在调用 adapter/transport 前拒绝；不创建 evidence。
- unknown adapter、429、timeout、provider safe error 或 collection failure 不进入 trigger。
- RawItem persistence 失败时事务不推进 cursor，也不创建 evidence。
- missing sidecar、invalid projection 或 provenance/identity mismatch 不创建 evidence，并返回固定 safe
  failure。
- 单个失败不得伪装成功；不得自动重试、调度或请求替代 Provider。

## 5. DB/schema 边界

- 复用既有 Source、SourceAccount、CollectionRun、CollectionCursor、RawItem 与 evidence_items。
- 不新增或修改 migration、ORM model、table、column、index、constraint。
- 不新增 durable projection table；sidecar 是明确的 in-memory mock integration seam。
- Evidence persistence 只由既有 EvidenceWriteService 负责。

## 6. 严格非范围

- 不请求真实 API，不读取 `.env`，不执行 `provider_capture.py --execute`。
- 不读取或提交 raw capture/`local_evaluation/`。
- 不实现 real Marketaux/Finnhub/EIA/SEC adapter 或其他 Provider。
- 不实现 scheduler、Celery wiring 或自动 collection dispatch。
- 不实现 formal normalization、semantic/cross-provider dedup、clustering、Event 或 AI。
- 不实现 Telegram、investment recommendation、Portfolio/Holding 或交易动作。
- 不启动 SPEC-0022；SPEC-0005 X Source 范围不变。

## 7. 测试要求

使用 PostgreSQL semantics 与 Redis lock，mock transport only，覆盖：

1. mocked Marketaux → successful CollectionRun/RawItem/cursor → projection → evidence_items；
2. 同一成功 run 再处理返回 duplicate，不增加 evidence row；
3. RawItem persistence failure 不创建 evidence；
4. missing projection 不创建 evidence；
5. projection provenance mismatch 不创建 evidence；
6. 429 与 timeout 不创建 RawItem/evidence；
7. unauthorized/disabled Source 不调用 transport、不创建 evidence；
8. outcome/error 不含 content 或 secret；
9. source audit 禁止 requests/httpx/urllib/provider_capture/local_evaluation/scheduler/OpenAI/Telegram/
   recommendation dependency；
10. existing fake adapter、collection、projection trigger 与 EvidenceWriteService regressions 保持通过。

## 8. 验收标准

- [x] Python end-to-end mock orchestrator 与 content-free observer/sidecar 已实现。
- [x] CollectionRunner 成功后显式触发既有 Evidence Pipeline/Write Service。
- [x] RawItem persistence failure、provider errors 与 authorization gate 不创建 evidence。
- [x] missing/mismatch projection fail closed。
- [x] duplicate processing 不重复 evidence row。
- [x] 无 migration/ORM/schema change。
- [x] 无真实 API、`.env`/capture、real adapter、scheduler、dedup/Event/AI/Telegram。
- [x] SPEC-0022 未启动，Foundation v2.1-FROZEN 未修改。
- [ ] Reviewer/CI/完整验证与安全 review package PASS。

## 9. Verification Evidence

以本 PR diff、mock-only PostgreSQL/Redis tests、existing regressions、source audit、Foundation validator
与安全 review package 为证。未请求 API、读取 `.env`/capture/`local_evaluation/`，未修改 schema。

## 10. Review History

| Round | Result | Evidence | Resolution |
|---|---|---|---|
| 1 | IN REVIEW | 本 implementation PR、CI 与 review package | 等待用户/ChatGPT Implementation Review |

## 11. 后续门禁

本 SPEC PASS 只批准 mocked Marketaux end-to-end integration。真实 Adapter、credential wiring、scheduler、
其他 Provider、durable projection、formal normalization/dedup/Event/AI/Telegram 仍需独立 SPEC。
