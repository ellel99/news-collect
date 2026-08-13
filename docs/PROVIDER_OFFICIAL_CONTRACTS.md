# Provider Official Contracts

> Status note (2026-08-13)：Marketaux、Finnhub、EIA 与 SEC EDGAR 已完成当前批准的 bounded
> adapter/runtime/scheduler scope。本文中的 smoke/preflight 条目是合同证据，不应解读为实现仍未开始，
> 也不证明 multi-target production capability。新 target/operation 必须使用 SPEC-0041 提议的
> typed/versioned contract 并重新审核 quota、retention、cursor 与 request budget。

状态：preflight scaffold contract；未执行真实 smoke；不授权 Adapter implementation。

本文件只记录官方资料可支持的最小请求合同。套餐、额度、留存、内部 AI 使用与再分发规则以
用户实际签约条款为准；未由官方公开文档和用户 plan 共同确认的项目均为 Pending。

## NewsAPI.ai / Event Registry

- 当前状态：`future / blocked`；保留官方合同研究与 dry-run scaffold，但不允许真实 smoke 或
  `--execute`。未来恢复必须经过用户/ChatGPT 独立 Review。
- 官方资料：[API documentation](https://newsapi.ai/documentation)、
  [Article search](https://newsapi.ai/documentation?tab=searchArticles)、
  [official Python SDK](https://github.com/EventRegistry/event-registry-python)。
- 认证：官方 REST API 使用 `apiKey`，`getArticles` 支持 GET / POST。脚手架选择 POST JSON
  body，避免把 key 放入命令行和 URL；第一次真实 bounded smoke 必须验证这一认证方式。
  如果认证失败，必须停止并交由用户/ChatGPT Review，不得自动 fallback 到 GET 或其他方式。
- 最小 endpoint：`POST https://eventregistry.org/api/v1/article/getArticles`。
- 最小参数：`action=getArticles`、`keyword`、`articlesPage=1`、`articlesCount=1`、
  `articlesSortBy=date`、`dataType=["news"]`、`forceMaxDataTimeWindow=7`、
  `resultType=articles`、`apiKey`。
- 官方响应合同：article search response 位于 `articles.results`；smoke 检查实际 item 是否
  提供 `uri`、`title`、`url`、`dateTime` / `dateTimePub`、`source` 等合同候选字段，只记录
  实际字段名与数量，不输出 article 值。
- 额度：官方文档说明调用按 token 计费，免费注册额度和每次调用消耗可变化；实际 plan、quota
  与 token 消耗必须由用户填写并在 smoke 后核对。
- 保存边界：Pending user plan / terms verification。默认不保存 response、title、body 或 URL；
  `NEWSAPI_AI_ALLOWED_RETENTION` 和 `NEWSAPI_AI_INTERNAL_AI_ALLOWED` 未确认时不得实现。
- 未确认项：plan-specific rate limit headers、numeric quota、retention、redistribution、
  internal AI use、timeout/retry、stable ID 和分页恢复的实际响应证据。

## Marketaux

- 官方资料：[API documentation](https://www.marketaux.com/documentation)、
  [pricing](https://www.marketaux.com/pricing)。
- 认证：官方 `api_token` query parameter；key 只能来自环境变量，redacted report 不包含值。
- 最小 endpoint：`GET https://api.marketaux.com/v1/news/all`。
- 最小参数：`search=technology`、`limit=1`、`page=1`、`api_token`。
- 官方响应合同：顶层 `meta` 与 `data`；`data` 为 article list。smoke 检查 `uuid`、`title`、
  `description` / `snippet`、`url`、`published_at`、`source` 等候选字段，只记录字段名和数量。
- 额度：官方文档列出 `X-UsageLimit-*`、`X-RateLimit-*` headers，并记录 402 usage limit 与
  429 recent request limit；具体数值取决于 plan。
- 保存边界：Pending user plan / terms verification；不得把 API 可返回正文等同于获得保存或
  再分发授权。
- 未确认项：用户 plan、daily limit、retention、redistribution、internal AI use、
  plan-specific timeout/retry 和实际 header 行为。

## Finnhub

- 官方资料：[API documentation](https://finnhub.io/docs/api)、
  [quote endpoint](https://finnhub.io/docs/api/quote)、
  [rate limits](https://finnhub.io/docs/api/rate-limit)。
- 认证：官方支持 `token` 参数或 `X-Finnhub-Token` header；脚手架选择 header，避免 URL 泄密。
- 最小 endpoint：`GET https://finnhub.io/api/v1/quote`，参数 `symbol=AAPL`。
- 官方响应合同：quote JSON 的文档字段包括 `c`、`d`、`dp`、`h`、`l`、`o`、`pc`、`t`；
  preflight 只记录字段名，不记录值，也不实现 Market Validation。
- 额度：官方文档说明 plan limit，并有全局请求保护与 HTTP 429；用户必须填写实际 plan 和
  rate limit。
- 保存边界：Pending user plan / terms verification；本 scaffold 不保存行情值。
- 未确认项：plan-specific quota、header 行为、retention、redistribution、timeout/retry。

## EIA Open Data

- 官方资料：[API v2 technical documentation](https://www.eia.gov/opendata/documentation.php)、
  [Open Data](https://www.eia.gov/opendata/)、[registration terms](https://www.eia.gov/opendata/register.php)。
- 认证：API v2 必须使用注册 key；官方明确 key 必须在 URL query 中，不能放入 header。
- 最小 endpoint：
  `GET https://api.eia.gov/v2/electricity/retail-sales/data/`。
- 最小参数：`api_key`、`data[]=price`、`frequency=monthly`、`length=1`、
  `sort[0][column]=period`、`sort[0][direction]=desc`。
- 官方响应合同：顶层含 `response`、`request`、`apiVersion`；数据位于
  `response.data`，smoke 检查 `period`、facet metadata 和请求的 `price` 字段，并只记录
  字段名；`response` 还包含 `total`、frequency/date metadata。
- 分页与边界：API v2 支持 `length`、`offset`、sort、start/end；JSON 单次最多 5,000 rows。
  scaffold 固定为 1 row，不进行 backfill。
- 保存边界：EIA 数据复用仍须遵守 EIA Copyrights and Reuse Policy 与 API Terms；本 scaffold
  不保存值。
- 未确认项：numeric rate limit、quota headers、timeout/retry 和用户注册条款的最终确认。

## SEC EDGAR

- 官方资料：[EDGAR APIs](https://www.sec.gov/edgar/sec-api-documentation)、
  [Developer Resources / Fair Access](https://www.sec.gov/developer)、
  [Privacy and Security Policy](https://www.sec.gov/about/privacy-information#security)。
- 认证：`data.sec.gov` JSON API 无 API key。自动访问必须使用可识别 User-Agent；本项目要求
  本地 `SEC_USER_AGENT` 和 `SEC_CONTACT_EMAIL`。
- 最小 endpoint：
  `GET https://data.sec.gov/submissions/CIK0000320193.json`，用于 runbook 中固定 AAPL/CIK
  的结构 smoke，不进行搜索或批量下载。
- 官方响应合同：company submissions JSON 含 `cik`、`name`、`tickers` 等公司 metadata，
  `filings.recent` 是 columnar filing fields；smoke 检查 `accessionNumber`、`filingDate`、
  `reportDate`、`acceptanceDateTime`、`form`、`primaryDocument` 等字段，只记录字段名和
  column count。
- Fair Access：官方当前指引总计不超过 10 requests/second，并要求高效、最小化下载；本
  bounded smoke 仅一次请求。
- 保存边界：公开 EDGAR 数据的后续保存仍须由对应 SPEC 固定；本 scaffold 不保存 response。
- 未确认项：响应 headers、timeout/retry、长期 retention policy 和后续具体 evidence scope。

## Cross-provider safety contract

1. 默认 dry-run，不产生网络请求。
2. `--execute` 与所需环境变量同时存在才可能请求；secret 不接受 CLI 参数。
3. report 只含 provider、endpoint family、HTTP status、JSON validity、字段名、count、
   rate-limit header presence、Retry-After presence 和 PASS/BLOCKED/FAIL。
4. 不保存完整 response，不打印真实 title/body/URL/value，不访问 source page。
5. preflight 不是 Adapter，不注册 adapter key、不写数据库、不运行 collection。
6. PASS 只证明最小 endpoint 的结构 smoke 通过；许可、额度和留存未获用户/ChatGPT Review
   前仍不得实现。
7. Optional plan/quota/retention/internal-AI metadata 不参与请求认证门禁；缺失时不会阻塞
   dry-run 或获准平台的最小 smoke，但会阻塞后续合同 PASS 和 Adapter implementation。
8. 当前执行序列仅为 Marketaux → Finnhub → EIA → SEC；NewsAPI.ai 为 future / blocked。
