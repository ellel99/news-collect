# Source Catalog

版本：2.1-FROZEN  
状态：Structure frozen; source records living

## 1. 使用规则

本目录记录目标来源，不代表接入权限已获得。任何 Adapter 开发前必须在对应 SPEC 中确认：

- 合法接入方式；
- 授权与订阅状态；
- 可保存内容范围；
- 目标延迟；
- 稳定外部 ID；
- 失败与替代方案。

未知信息必须标记 `TBD`、`unknown` 或 `unverified`，不得猜测。列入 Catalog 不等于批准开发；只有 Active SPEC 明确列入的来源才能实现。

每个真实来源 SPEC 还必须固定：Endpoint/Feed、认证引用、轮询或流式模式、调度频率、请求超时、限流与退避、Cursor、水位、稳定外部 ID、规范化 URL、Parser 合同和版本、内容保留等级、Fallback、合约测试和真实验证证据。

## 1.1 供应商无关规则

- 任何 provider 都必须通过 `SYSTEM_DESIGN.md` 定义的 Connector / Unified Ingestion Gateway 合同接入。
- GDELT、NewsAPI.ai / Event Registry、Finnhub 或任何媒体/市场数据供应商都不是不可替换核心依赖。
- 下游 Normalization、Deduplication、Event、AI、Notification 不得导入具体供应商 SDK 或依赖其原始 payload。
- Catalog 中出现 provider 只表示候选，不表示已授权、已接入、可获得全文或满足实时 SLA。

## 2. 新闻媒体

| Code | 来源 | 官网 | 首选接入 | 默认内容范围 | 状态 |
|---|---|---|---|---|---|
| `reuters` | Reuters | `https://www.reuters.com/` | 正式授权 API/Feed；其次公开 RSS/页面 | 标题、摘要、时间、URL；正文按授权 | access_tbd |
| `bloomberg` | Bloomberg | `https://www.bloomberg.com/` | 正式商业数据产品；其次合法公开元数据 | 标题、摘要、时间、URL；正文按授权 | access_tbd |
| `wsj` | The Wall Street Journal | `https://www.wsj.com/` | 合法订阅/Feed/公开元数据 | 标题、摘要、时间、URL；正文按授权 | access_tbd |
| `cnbc` | CNBC | `https://www.cnbc.com/` | 官方 Feed、公开 API 或合法页面 | 标题、摘要、时间、URL；公开正文按政策 | access_tbd |

约束：

- 不绕过付费墙、登录、验证码或反爬；
- 来源不可合法接入时标记 `blocked`，不得用违规方案替代；
- 同一转载内容保留原始发布者和当前承载来源；
- 报道正文不可得时，可用标题、摘要和其他可信 Evidence 分析，但必须标记完整性。

## 3. X 目标人物

| Person | 预期 handle | 稳定用户 ID | 身份状态 | 默认采集 |
|---|---|---|---|---|
| 赵长鹏 | `@cz_binance` | TBD | unverified | 原创、回复、引用、转发 |
| 孙宇晨 | `@justinsuntron` | TBD | unverified | 原创、回复、引用、转发 |
| Donald Trump | `@realDonaldTrump` | TBD | unverified | 原创、回复、引用、转发 |
| Elon Musk | `@elonmusk` | TBD | unverified | 原创、回复、引用、转发 |
| Jensen Huang | `@JensenHuang`（待官方核验） | TBD | unverified | 原创、回复、引用、转发 |

实现前必须通过官方或可靠渠道验证 handle 与稳定平台用户 ID。账号改名时更新 handle，不改变历史归属。

删除检测是 best effort，不保证发现所有已删除帖子。视频默认只保存合法可用的元数据和原始链接，不批量下载文件。

## 4. 建议的一手来源队列

这些来源通常比媒体转述更直接，应按后续 SPEC 排期，不属于 SPEC-0001 的接入范围：

- SEC EDGAR；
- 上市公司 Investor Relations；
- Federal Reserve / FOMC；
- U.S. Bureau of Labor Statistics；
- U.S. Bureau of Economic Analysis；
- White House；
- U.S. Treasury；
- U.S. Department of Commerce；
- U.S. Energy Information Administration；
- OPEC；
- 交易所和公司正式公告。

## 4.1 候选低成本/公开发现来源

| Code | 用途 | 候选模式 | 状态 |
|---|---|---|---|
| `gdelt` | 全球新闻发现与线索 | Polling；仅有限时间窗回补 | SPEC-0004 preimplementation verification；未实现、非全文授权、非核心依赖 |
| `rss_atom` | 媒体、公司、机构 Feed | Polling | planned；逐 Feed 审核 |
| `company_ir` | 公司官网与 Investor Relations | Polling | planned；逐公司审核 |
| `sec_edgar` | SEC 文件与公告 | Polling / Backfill | planned；独立 SPEC |
| `official_agencies` | 政府、监管、央行、能源机构 | Polling / Webhook（如官方支持） | planned |
| `finnhub_candidate` | 可替换市场/财务数据候选 | Polling / Streaming（按授权） | planned；不是核心依赖 |
| `event_registry_candidate` | NewsAPI.ai / Event Registry 候选 | Polling / Streaming（按产品） | planned；后续评估 |

公开网页不等于允许批量采集全文。CNBC、Reuters 公开线索等仍需逐来源确认 robots、条款、保留和 parser 合同。

### GDELT SPEC-0004 核验状态

- 用户选择 GDELT 作为唯一 Polling Source Pilot candidate；这不表示已实现或已授权新闻全文。
- 试点选择官方 DOC 2.0 API 的 `ArticleList` JSON endpoint family，精确 query/endpoint 在实现前
  contract review 中固定；本轮未请求 API 或生产数据。
- 当前核验目标是 GDELT Project legacy / public DOC 2.0，不是 GDELT Cloud；不得混用两者的
  API 或条款，GDELT Cloud 需要未来独立 SPEC 或规格修订与评估。
- 默认 `access_level = LINK_ONLY`。只允许 title、GDELT/来源 metadata、source URL、时间、
  确定性 ID/hash 和最小 raw reference；不得保存第三方新闻正文。
- GDELT 官方 Terms 允许使用和再分发 GDELT 发布的数据集，但要求引用 GDELT 并链接官网；
  该许可不替代原始新闻发布者的版权或访问许可。
- DOC API 官方文档提供 `TIMESPAN`、`STARTDATETIME`、`ENDDATETIME` 和 `MAXRECORDS`，但没有
  文档化通用 pagination cursor/offset；只能做有界时间窗与重叠恢复，不实现通用 Historical
  Backfill。
- 官方确认 DOC/Context APIs 会 rate limit，但未公布数值配额；numeric limit、timeout、retry
  参数和真实响应字段仍是 implementation blocker。
- 权威证据与剩余风险见 `spec/SPEC-0004.md` 的 GDELT Verification Evidence。

## 4.2 候选商业升级来源

- Reuters / LSEG 实时新闻流；
- Factiva；
- LexisNexis；
- NewsAPI.ai / Event Registry 商业能力；
- WebSocket 新闻服务、Webhook 推送源和其他商业实时流；
- 可替换的市场行情与财务数据供应商。

这些只保留 Connector 能力入口。未取得合同、授权、字段说明和测试环境前一律为 `access_tbd`，不得写成已实现或承诺 SLA。

## 5. 关联来源

- Donald Trump 的重要声明可能首发于 Truth Social；
- Jensen Huang 相关公司级公告可能来自 NVIDIA 官方账号、新闻室和 IR；
- 人物与公司关联来源只能作为新增 SourceAccount，不能冒充人物本人。

是否加入由单独 SPEC 决定。

## 6. Source 状态

```text
planned
access_tbd
authorized
implemented
degraded
blocked
disabled
```

每个 Source/SourceAccount 的实现记录至少包含：

- `access_method`
- `authorization_status`
- `terms_checked_at`
- `retention_class`
- `poll_or_stream_mode`
- `target_latency`
- `rate_limit_policy`
- `request_timeout_seconds`
- `retry_policy`
- `cursor_strategy`
- `stable_external_id`
- `canonical_url_strategy`
- `parser_contract`
- `parser_version`
- `test_evidence`
- `fallback`
- `owner_note`
