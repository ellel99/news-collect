# Provider Decision

## Status

- Decision authority：ChatGPT / 用户确认。
- Codex 职责：按已确认结论维护文档和执行用户明确授权的验证；不负责重新评估、选择或替换
  provider。
- 生效范围：产品来源分层方向与 SPEC-0004 provider realignment。
- 实现状态：未开始。

## Confirmed Provider Roles

| Role | Confirmed Provider / Layer | Current Engineering Status |
|---|---|---|
| SPEC-0004 Primary Provider | NewsAPI.ai / Event Registry | selected primary candidate；pending user credentials and contract data |
| Secondary Financial News Provider | Marketaux | secondary candidate；不在当前 SPEC-0004 implementation 范围 |
| Market Validation Provider | Finnhub | market validation candidate；Market Validation 实现仍需适用的 Foundation revision、Freeze Review 和独立 SPEC |
| Official Evidence Layer | SEC EDGAR / EIA / Company IR / Official RSS | confirmed evidence-source layer；逐来源授权、合同与独立 SPEC 仍待完成 |
| Historical evaluated provider | GDELT Project DOC 2.0 | runtime blocked；future evaluation only；不再是 SPEC-0004 primary pilot |

GDELT 的既有官方文档核验、两轮 bounded smoke 和 failure analysis 仅作为历史 evidence 保留。
不得继续 GDELT smoke，不得据此开始任何 GDELT implementation。

## Required User Inputs Before Continuing

用户必须提供并确认：

1. NewsAPI.ai / Event Registry API key（只能通过批准的 secret channel，不得提交到 Git）。
2. plan 名称。
3. quota / token limit。
4. allowed retention，包括允许保存的字段、期限、内部处理和再分发边界。
5. 是否允许 internal AI analysis。

在以上信息齐备并完成合同 Review 前，不得请求 NewsAPI.ai / Event Registry API。

## Next Authorized Sequence

统一流程：

```text
用户完成平台注册并保存凭证
→ 对单个平台执行 bounded smoke
→ 用户和 ChatGPT 审核 smoke 结果
→ 确认字段、额度、许可和保存边界
→ 编写或激活对应 SPEC
→ Codex 实现 Provider Adapter
→ 测试、审查和合并
```

平台执行顺序：

1. NewsAPI.ai / Event Registry。
2. Marketaux。
3. Finnhub。
4. EIA Open Data。
5. SEC EDGAR。

顺序只规定注册、合同核验、bounded smoke 和后续 SPEC 的先后，不授权并行接入或越过阶段
边界。尤其 Finnhub 的 Market Validation implementation 仍须适用的 Foundation revision、
Freeze Review 和独立 SPEC。

当前只允许完成 provider-decision 文档纠偏及 Review。用户提供 credentials、plan、quota、
allowed retention 和 internal AI analysis 决策后，才能另行授权 NewsAPI.ai / Event
Registry bounded smoke。其余平台必须等待前一平台 Review 结论与用户明确授权，不得由
SPEC-0004 顺带 smoke 或实现。

## Bounded Smoke Contract

每个平台必须独立执行；一次授权只覆盖一个明确 endpoint family、有限请求数量和最小结果数。
Smoke 不访问新闻原文页，不保存完整 response、真实 secret 或未授权正文。

必须记录的验证字段：

| 类别 | 必须记录 |
|---|---|
| 请求边界 | Provider、官方 endpoint family、HTTP method、UTC timestamp、请求次数、timeout；query/参数需脱敏 |
| HTTP | status、redirect count、`Content-Type`、`Retry-After`、quota/rate-limit headers 是否存在 |
| 认证 | 是否使用用户提供的 secret reference、认证是否成功；不得记录 key/token 值 |
| 响应结构 | JSON validity、顶层字段名、item list 字段名、result count；不记录真实标题、正文或 URL 值 |
| 最小内容合同 | stable external ID、title、summary availability、source URL、`published_at`、`source_updated_at` 的存在性与 nullable 规则 |
| 恢复合同 | pagination/cursor/token、watermark/time window、page/result limit、有限 backfill 能力 |
| 额度与失败 | plan、quota/token 消耗、429/Retry-After、认证错误、5xx、timeout 的可分类证据 |
| 权利与保存 | access level、allowed retention、保存期限、内部处理、再分发和 attribution 边界 |
| 安全证据 | 未访问 source page、未提交 secret/response、未运行 collection、未实现 adapter |

Smoke PASS 必须同时满足：

1. 获得符合获批认证方式的成功 HTTP 响应和有效 JSON。
2. schema 足以映射 provider-neutral `RawItemEnvelope`，且 downstream 无需读取 provider raw
   payload。
3. stable external ID 或经用户/Reviewer 批准的确定性 fallback 已明确。
4. 时间、pagination/cursor、水位、限额和 retry 边界已确认；未知项有明确 fail-closed 规则。
5. access、license、allowed retention、内部处理和再分发边界已由用户/ChatGPT Review 确认。
6. 没有 secret、完整 response、受限内容或真实数据文件进入 Git。
7. 用户和 ChatGPT 对 evidence 给出 PASS，并明确授权编写或激活实现 SPEC。

任何认证失败、无效/不足 schema、额度不明、许可或 retention 未确认、持续 429/timeout、
安全边界违反或关键字段无法映射，均不得写成 PASS。结果为 Pending/Blocked 时不得开始
adapter implementation。

## Non-Scope and Safety

- 本决策不授权代码、测试、迁移、schema、ORM、依赖或 collection 变更。
- 用户对 internal AI analysis 的回答只用于合同/许可规划，不授权当前 AI / LLM、Event、
  Market Validation 或 Research Recommendation 实现。
- 不提交 API key、token、cookie、secret 或真实 provider response。
- provider 必须保持可替换，downstream 不得直接依赖 provider SDK 或 raw payload。
- Foundation v2.1-FROZEN 继续生效。
