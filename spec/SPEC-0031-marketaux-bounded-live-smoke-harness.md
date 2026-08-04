# SPEC-0031 — Marketaux Bounded Live Smoke Harness

Status：Bundled — Implementation Review in PR #30

Phase：Phase 1 — Manual Bounded Live Smoke Harness

Foundation：v2.1-FROZEN（unchanged）

Active delivery：SPEC-0030 combined PR；本 SPEC 不创建第二个 Active SPEC 或独立 PR。

## 1. 目标

在 PR #30 内提供显式、可审计、默认安全的 Marketaux manual smoke CLI，以验证 SPEC-0030 real
adapter/HTTP boundary。默认 dry-run；只有操作者显式传入 `--execute` 且 process environment 中存在
`MARKETAUX_API_TOKEN` 时才允许一次 bounded request。

## 2. 执行合同

```bash
python3 scripts/marketaux_live_smoke.py
MARKETAUX_API_TOKEN=... python3 scripts/marketaux_live_smoke.py --execute --limit 1
```

- dry-run 不读取 token、不构造 HTTP transport、不请求 API，只输出 safe plan。
- execute 只从 process environment 读取 token；不读 `.env`、env file、CLI secret 或 config secret。
- limit 默认 1，允许 1–3；超限在 credential/network 前 fail closed。
- execute 使用 `MarketauxRealAdapter` + `HttpxProviderTransport`；无循环、分页、调度或 DB write。
- 本 PR、CI、pytest、package review 都不得运行真实 execute；真实执行仍需用户单独明确授权。

## 3. Safe summary

只输出 provider、mode、PASS/BLOCKED/DRY_RUN、limit、item count、has_more/cursor/header presence 等
结构信息和固定 safe error code。不得输出 title/body/URL/snippet/description、raw response、完整 request
URL、token/Authorization/secret、provider item values 或 SQL/DB 数据。response 不写文件。

## 4. 严格非范围

- 不读 raw capture/`local_evaluation/`，不执行 `provider_capture.py --execute`。
- 不写 RawItem/evidence_items 或任何 DB，不接 collection runner/scheduler。
- 不实现 Finnhub/EIA/SEC、formal normalization、dedup、Event、AI、Telegram 或 investment advice。
- 不修改 migration/ORM/schema；不启动 SPEC-0022。

## 5. 测试与验收

- [x] dry-run 不访问 environment secret 或 transport。
- [x] execute missing token fail closed。
- [x] limit > 3 fail closed。
- [x] mocked execute path 输出 safe summary。
- [x] summary 不含 token、URL/title/body/snippet/raw response。
- [x] source audit 确认不读 env file/local capture，不写 DB，不依赖 scheduler/dedup/Event/AI/Telegram。
- [x] tests 不访问真实网络。
- [ ] PR #30 Reviewer/CI/安全 review package PASS。

## 6. Review History

| Round | Result | Evidence | Resolution |
|---|---|---|---|
| 1 | IN REVIEW | PR #30 combined implementation、mock tests 与 review package | 等待 combined Review |
