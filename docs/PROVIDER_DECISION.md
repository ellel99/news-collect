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

1. 当前只允许完成本 provider-decision 文档纠偏及 Review。
2. 用户提供 credentials、plan、quota、allowed retention 和 internal AI analysis 决策后，
   才能另行授权一次 NewsAPI.ai / Event Registry bounded smoke。
3. bounded smoke 只能验证 endpoint、认证、quota headers、JSON schema 和最小 retention
   contract；不得访问新闻原文页或保存未授权内容。
4. bounded smoke 成功并通过 Review 前，不得开始 adapter implementation、注册 adapter key、
   运行 collection 或写入真实数据。
5. Secondary、Market Validation 和 Official Evidence providers 不得由本 SPEC 顺带接入。

## Non-Scope and Safety

- 本决策不授权代码、测试、迁移、schema、ORM、依赖或 collection 变更。
- 本决策不授权 AI / LLM、Event、Market Validation 或 Research Recommendation 实现。
- 不提交 API key、token、cookie、secret 或真实 provider response。
- provider 必须保持可替换，downstream 不得直接依赖 provider SDK 或 raw payload。
- Foundation v2.1-FROZEN 继续生效。
