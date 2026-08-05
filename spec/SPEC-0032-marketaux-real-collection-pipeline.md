# SPEC-0032 — Marketaux Real Collection Pipeline

Status：Active — Implementation Review

Phase：Phase 1 — Manual Marketaux Real Collection-to-Evidence Pipeline

Foundation：v2.1-FROZEN（unchanged）

Depends on：SPEC-0026–0030（Completed）；SPEC-0031 bundled completion

## 1. 目标

复用已审核组件，提供一次显式 manual runtime：

```text
MarketauxRealAdapter
→ ProviderAdapterRegistry
→ CollectionRunner
→ RawItem + cursor checkpoint
→ InMemoryProviderProjectionSidecar
→ RawItemEvidencePipelineTrigger
→ EvidencePipelineService
→ EvidenceWriteService
→ evidence_items
```

本 PR 写 Python implementation code，不是 docs-only。默认 dry-run 完全 inert；只有操作者显式
`--execute` 才读取 process environment credential、访问一次 Marketaux 并写既有 DB schema。

## 2. Runtime wiring

- `build_marketaux_real_pipeline` 只做显式 composition：注册 `MarketauxRealAdapter`、注入 real/mock
  transport、CollectionRunner 与 content-free sidecar；不另写 parallel pipeline。
- `MarketauxRealCollectionPipeline` 委托既有 end-to-end orchestrator，不直接写 RawItem/evidence_items。
- manual target resolver 只允许恰好一个 enabled account，其 Source 必须 enabled、`access_method =
  marketaux` 且 authorization 为 authorized/implemented；零个或多个 target 均 fail closed。
- `--doctor` 只读检查 Marketaux Source/SourceAccount 与 eligible target 数量；`--bootstrap-target`
  在干净 DB 中幂等创建最小 metadata-only target，且不读取 token、不请求 Provider、不改变 schema。
- 已存在唯一 target 返回 `already_exists`；多个 eligible target 必须以
  `marketaux_target_not_unique` fail closed。
- RawItem/cursor 仍由 CollectionRunner transaction 写入；evidence 仍只通过既有 Pipeline/Write Service。

## 3. Manual command

```bash
python3 scripts/marketaux_real_collection_smoke.py
python3 scripts/marketaux_real_collection_smoke.py --doctor
python3 scripts/marketaux_real_collection_smoke.py --bootstrap-target
MARKETAUX_API_TOKEN=... python3 scripts/marketaux_real_collection_smoke.py --execute --limit 1
```

- 默认 limit 1，最大 3；超限在 token/DB/network 前拒绝。
- dry-run 不读 token、不构造 Settings/DB/Redis/HTTP transport、不写 DB，只输出 safe plan。
- execute 只从 process environment 读取 token，并通过 `_env_file=None` 禁止 Settings 读取 `.env`。
- summary exact boundary：provider/status/collection_status/raw/evidence counts/cursor presence/
  response_saved/safe errors/db_written/token_read。
- summary 不含 token、完整 URL、raw response、title/body/snippet/description/article URL/provider payload。
- response 不保存到文件；不提交 live output。

## 4. Failure behavior

- missing token、invalid limit、missing/ambiguous target 在网络前 fail closed。
- target 错误区分 `marketaux_target_missing`、`marketaux_target_not_unique`、
  `marketaux_target_disabled`、`marketaux_target_unauthorized` 与 `marketaux_account_missing`。
- 429/timeout/provider error 不写 RawItem/evidence_items。
- RawItem persistence failure 不推进 cursor、不触发 evidence。
- duplicate processing 通过既有 provider-scoped idempotency 返回 duplicate，不增加 evidence row。
- 所有错误只输出固定 safe code，不回显 input、SQL params 或 provider data。

## 5. 严格非范围

- CI/pytest/package review/default command 不请求真实 API；真实 execute 需用户另行授权。
- 不读 `.env`、capture/`local_evaluation/`，不执行 `provider_capture.py --execute`。
- 不保存 raw response，不下载 article 页面或全文。
- 不实现 scheduler、Telegram、AI、formal normalization、dedup、clustering 或 Event。
- 不实现 Finnhub/EIA/SEC 或其他 Provider。
- 不修改 migration、ORM model 或 DB schema；不启动 SPEC-0022。

## 6. 测试与验收

- [x] default dry-run safe/inert；missing token 与 limit >3 fail closed。
- [x] doctor 空 DB fail closed；bootstrap 空 DB 创建 target、重复运行幂等、多 target fail closed。
- [x] bootstrap 不读 token、不请求 API；bootstrap 后 mocked pipeline 写 RawItem/evidence_items。
- [x] mocked Marketaux real adapter → CollectionRunner → RawItem → evidence_items 成功。
- [x] 429/timeout 不写 RawItem/evidence_items。
- [x] RawItem persistence failure 不写 evidence。
- [x] duplicate processing 不重复 evidence。
- [x] outcome/summary 不含 content、URL、secret 或 raw response。
- [x] source audit 无 `.env` file、capture、scheduler/Telegram/AI/dedup/Event dependency。
- [x] PostgreSQL/Redis semantics；tests mock transport only，不访问 Provider。
- [x] 无 migration/ORM/schema change；SPEC-0022 未启动。
- [ ] Reviewer/CI/完整验证与安全 review package PASS。

## 7. Verification Evidence

以本 PR source diff、mock-only PostgreSQL/Redis integration tests、full regression、default dry-run、
Foundation validator 与 committed-snapshot review package 为证。本 PR/CI 未执行真实 API。

## 8. Review History

| Round | Result | Evidence | Resolution |
|---|---|---|---|
| 1 | IN REVIEW | 本 implementation PR、CI 与 review package | 等待用户/ChatGPT Implementation Review |
| 2 | REQUEST CHANGES | 用户本地 execute 被 target resolution 阻塞 | 增加 safe doctor、幂等 bootstrap 与细化 target diagnosis；等待复审 |
