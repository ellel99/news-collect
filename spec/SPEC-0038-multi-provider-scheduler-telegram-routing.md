# SPEC-0038 — Multi-provider Scheduler + Telegram Routing

Status：Active — Implementation Review

Phase：Phase 1 — Information Collection & Push

Foundation：v2.1-FROZEN（unchanged）

Depends on：SPEC-0035、SPEC-0037（Completed）

## 1. 目标

把已批准的 Marketaux、Finnhub、EIA Open Data、SEC EDGAR ingestion 链路接入一个最小、隔离、
可恢复的 Celery Beat 调度入口，并把新 ContentItem 按 Provider 类型路由到 Telegram：

`Provider → CollectionRunner → RawItem → EvidenceItem → ContentItem → Notification → Telegram`

本 SPEC 写 Python implementation code。它不新增 Provider，不修改 schema，不实现 AI、投资建议、
formal dedup、Event 或 clustering，也不启动 SPEC-0022。

## 2. 调度合同

- Beat task：`multi_provider.telegram.run`；默认
  `MULTI_PROVIDER_SCHEDULER_EXECUTE=false`，因此完全 inert。
- 每次 cycle 固定串行隔离 Marketaux → Finnhub → EIA → SEC EDGAR；单家失败不得阻断其他 Provider。
- Redis `SET NX EX` cadence marker 为每家独立节流：Marketaux 300 秒、Finnhub 900 秒、EIA
  21600 秒、SEC 1800 秒，均可由 process environment 配置。
- not due 与成功空轮询均为 `NO_NEW_ITEMS`，不发送 Telegram；missing credential/target 只阻断对应
  Provider。
- execute 才读取 process environment credential；不读取 `.env`。tests/CI/package review 不启用
  execute，不访问真实 Provider 或 Telegram。
- 共享 ingestion pipeline 保留正常 `has_more`/pagination 语义；本 scheduler 不继承 SPEC-0037
  verifier 的 `max_batches=1` 限制。

## 3. Visible ContentItem 与 routing

- Marketaux 复用已批准的 metadata-only visible news ContentItem。
- Finnhub 只生成 content-safe market-data update title、symbol 与 timestamp；不扩散 quote numeric value。
- EIA 只生成 content-safe official energy update title、period/geography/sector presence；不扩散 numeric value。
- SEC 复用 metadata-only filing ContentItem；不下载 filing body 或 primary document。
- Provider message label 分别为 News、Market data、Official energy data、Company filing。
- Telegram message只含批准的 title、source、time 与可选 canonical link；不含 token、raw response、
  quote/EIA value、filing body、article body、snippet 或 description。

## 4. 投递可靠性

- 复用现有 `notifications` 表与唯一 `dedup_key`，不新增 migration/ORM/schema。
- 新 ContentItem 通过 atomic insert claim；`SENT` 永久去重，不得重复发送。
- Telegram failure 写安全 failure code并保留 `FAILED`；bounded retry 最大三次。
- stale `SENDING` 超过 300 秒可原子恢复；达到上限的记录计入 exhausted，不无限重试。
- retry scan 独立于当前 Provider collection：即使某 Provider 本轮失败、not due 或 no-new-items，历史
  failed notification 仍可恢复。
- 不删除 Notification 绕过去重，不保存 Telegram response body，不静默丢失未推送状态。

## 5. 安全输出

manual command `scripts/multi_provider_scheduler_smoke.py` 默认 dry-run：不读 credential、不连接
DB/Redis、不访问网络、不写 DB。`--execute` 才启用 runtime。

安全 summary 只包含 overall/provider status、collection status、RawItem/EvidenceItem/ContentItem counts、
sent/failed/retry/exhausted counts、固定 safe error codes 与 `response_saved=false`。不得输出 credential、
完整 URL、raw response、正文或 Provider numeric values。

## 6. 测试与验收

- [x] 四 Provider 固定顺序执行，单家 exception 隔离。
- [x] missing credential 仅使对应 Provider `BLOCKED`。
- [x] Finnhub/EIA sanitized projection 生成 metadata-only ContentItem，不含 numeric value。
- [x] 四种 Provider Telegram formatter 使用独立安全标签。
- [x] 新 item 原子 claim；同一 ContentItem `SENT` 后不重复发送。
- [x] Telegram failure、bounded retry、stale `SENDING` recovery 与 exhaustion 有 PostgreSQL tests。
- [x] no-new-items 不发送；历史 retry 不依赖当前 collection 成功。
- [x] 默认 smoke 完全 inert，不读取 credential、不访问 runtime、不写 DB。
- [x] source audit 不依赖 `.env`、raw capture、AI、Event、clustering 或 recommendation。
- [ ] Reviewer PASS。

所有测试使用 mocked Provider/Telegram transports；不得在 CI/pytest/package review 中请求真实服务。

## 7. 非目标

- 不新增或重选 Provider；不实现 X、NewsAPI.ai 或 GDELT。
- 不修改 migration、ORM 或 DB schema。
- 不实现 AI、投资建议、Market Validation、formal dedup、Event、clustering 或 Telegram 管理 Bot。
- 不读取/提交 `.env`、local_evaluation、raw capture、token、raw response 或 live output。
- 不启动 SPEC-0022。

## 8. Review History

| Round | Result | Evidence | Resolution |
|---|---|---|---|
| 1 | IN REVIEW | scheduler、routing、reliable notification、mock/PostgreSQL tests 与 review package | 等待用户/ChatGPT Review |
