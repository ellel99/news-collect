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
- EIA/SEC adapter 的 `has_more=false` 是显式单请求门禁；本 SPEC 不实现 pagination。
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
- [x] EIA/SEC 多行 response 仍 `has_more=false`，不会触发第二请求。
- [x] source audit 无 scheduler/Telegram/AI/Event/dedup/local capture 依赖。
- [x] SPEC-0036 adapter/ingestion mock/PostgreSQL tests 保持通过。
- [x] 完整 mock/local 验证与 review package PASS。
- [ ] 三家 bounded live execute：2026-08-11 首次统一 execute 因当前 process environment 中四项
  credential 均 MISSING，在网络前安全 BLOCKED；0 Provider requests、0 DB writes。不得读取 `.env`
  绕过此门禁，需用户导出 process environment 后再次明确执行。
- [ ] Reviewer PASS。

## 6. Runtime Verification Evidence

- Bootstrap：Finnhub/EIA/SEC 均 `created`，eligible target count 均为 1。
- Doctor：三家均 `PASS`。
- Dry-run：三家均 `DRY_RUN`，`credential_read=false`、`request_enabled=false`、`db_written=false`。
- Execute attempt：三家均 `BLOCKED` / `provider_runtime_credential_missing`；没有发出真实请求。
- 未读取 `.env`，未保存 response/live output，未输出任何 credential、value、URL 或 body。

## 7. Review History

| Round | Result | Evidence | Resolution |
|---|---|---|---|
| 1 | IN REVIEW | unified runner、mock tests、bounded live safe summaries、review package | 等待用户/ChatGPT Review |
