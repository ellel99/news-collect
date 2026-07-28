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
