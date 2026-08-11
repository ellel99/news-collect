# SPEC-0037 — Multi Provider Runtime Verification

Status：Active — Implementation Review

Phase：Phase 1 — Bounded Runtime Verification

Foundation：v2.1-FROZEN（unchanged）

Depends on：SPEC-0036（Completed）

## 1. 目标

使用统一、默认 inert 的 runner，一次性验证既有 Finnhub、EIA、SEC EDGAR 正式 adapter/ingestion：

`Provider API → Adapter → CollectionRunner → RawItem → EvidenceWriteService → evidence_items`

SEC 继续按 SPEC-0036 写 metadata-only ContentItem。不得重复 raw capture/replay/preflight，不修改
adapter 架构、migration、ORM 或 DB schema。

## 2. Unified runner

`scripts/multi_provider_runtime_smoke.py` 串行、固定顺序运行 Finnhub → EIA → SEC：

- 默认：三个 Provider 均返回 DRY_RUN，不读 credential、不访问 DB/Redis/network、不写 DB。
- `--doctor`：只诊断三家 Source/SourceAccount target，输出安全 counts/status/errors。
- `--bootstrap-target`：幂等创建缺失 target；不读 credential、不请求 API。
- `--execute`：从 process environment 读取 credential，每家最多调用一次 adapter request，继续输出
  content-free safe summary；不读取 `.env`。

## 3. Bounded live contract

- Finnhub：`FINNHUB_API_KEY`，AAPL quote，limit=1；不输出 quote values，不做 market analysis。
- EIA：`EIA_API_KEY`，electricity monthly row，limit=1；不输出 numeric values，不 backfill。
- SEC：`SEC_USER_AGENT` + `SEC_CONTACT_EMAIL`，AAPL submissions/recent，limit=1；不下载或解析
  filing body，不回显 contact/User-Agent。
- EIA/SEC adapter 保留真实 `has_more` 合同语义（response 行数大于 limit 时为 true）。bounded
  verification 的单请求门禁由 verifier/orchestrator 负责：每个 executor 恰好调用一次，不读取
  `has_more` 发起第二次请求，不做 pagination 或 backfill。
- 共享 `MultiProviderIngestionPipeline` 默认不设置 batch 上限并保留正常 pagination；只有本 SPEC
  的 runtime `execute_provider` 显式注入 `max_batches=1`，production/scheduler path 不继承该限制。
- live summary 只含 provider/status/counts/booleans/fixed safe errors；不写 live output 文件。

## 4. 安全边界

- 不输出或保存 token/API key、SEC contact/full User-Agent、full URL、raw response、quote/EIA value、
  filing/article body、snippet/description。
- 不把 credential 写 DB；不读取/提交 local_evaluation 或 raw capture。
- tests/CI/package review 全部 mock-only，不得请求真实 Finnhub/EIA/SEC。
- 不修改 scheduler、Telegram routing；不实现 AI、投资建议、market/energy analysis、formal dedup、
  Event、clustering、其他 Provider或 SPEC-0022。

## 5. 测试与验收

- [x] unified default dry-run 不读 environment、不调用 inspector/executor。
- [x] doctor/bootstrap 串行覆盖三家且不调用 Provider executor。
- [x] mocked execute 串行、每家恰好一次、safe counts 符合预期。
- [x] EIA/SEC response 行数大于 limit 时 `has_more=true`，保留 Provider contract 语义。
- [x] unified verifier 即使收到 `has_more=true` 也只调用每个 Provider executor 一次，不触发
  pagination/backfill。
- [x] normal pipeline 在 `has_more=true` 时继续下一 batch；仅 verifier path 限制为一 batch。
- [x] SEC submissions snapshot cursor 使用独立 policy：same cursor 合法返回 no-new-items，newer
  cursor 推进，older cursor fail closed；Marketaux/Finnhub/EIA 继续使用 strict successor。
- [x] runtime summary 将成功空轮询报告为 `PASS` / `collection_status=no_new_items`，并只暴露
  collection run presence/status/fixed error code 等安全诊断。
- [x] source audit 无 scheduler/Telegram/AI/Event/dedup/local capture 依赖。
- [x] SPEC-0036 adapter/ingestion mock/PostgreSQL tests 保持通过。
- [x] 完整 mock/local 验证与 review package PASS。
- [ ] 三家 bounded live execute：Finnhub 与 EIA integrated ingestion 已由用户本地验证 PASS。SEC
  request 成功并 fetched 1 item，但首次 integrated ingestion 因 snapshot same-cursor 被旧 strict
  successor contract 阻断；本 PR 已修复，等待用户进行一次独立 SEC post-fix live verification。
- [ ] Reviewer PASS。

## 6. Runtime Verification Evidence

- Bootstrap：Finnhub/EIA/SEC 均 `created`，eligible target count 均为 1。
- Doctor：三家均 `PASS`。
- Dry-run：三家均 `DRY_RUN`，`credential_read=false`、`request_enabled=false`、`db_written=false`。
- Finnhub live integrated ingestion：PASS，RawItem=1、EvidenceItem=1。
- EIA live integrated ingestion：PASS，RawItem=1、EvidenceItem=1。
- SEC live request：Provider request 成功、`fetched_count=1`；不是 credential、User-Agent、network、
  target 或 HTTP failure。首次 integrated ingestion 被 snapshot same-cursor bug 阻断：
  `COLLECTION_CONTRACT_INVALID` / `cursor is not a direct successor`。当前 cursor 使用安全的
  `provider_item_id + published_at` 结构；具体值不作为实现逻辑或测试夹具。
- Fix：SEC same cursor → no-new-items；newer → advance；older → fail closed；runtime empty success 与
  safe collection diagnostics 已补齐。尚未声称 SEC post-fix live PASS。
- 未读取 `.env`，未保存 response/live output，未输出任何 credential、value、URL 或 body。

## 7. Review History

| Round | Result | Evidence | Resolution |
|---|---|---|---|
| 1 | IN REVIEW | unified runner、mock tests、bounded live safe summaries、review package | 等待用户/ChatGPT Review |
| 2 | REQUEST CHANGES | adapter `has_more` contract 与 verifier request bound 混淆 | 已恢复真实 `has_more`，单请求由 verifier 保证 |
| 3 | REQUEST CHANGES | SEC snapshot same cursor 被 strict successor 错判 | 已实现 SEC-specific snapshot policy、no-new-items 与安全诊断；等待 post-fix live verification |
