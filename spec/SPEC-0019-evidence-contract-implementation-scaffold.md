# SPEC-0019 — Evidence Contract Implementation Scaffold

状态：Active — Implementation Review
阶段：Phase 1 — Contract Implementation Scaffold
负责人：Project Owner
创建日期：2026-08-02
最后更新：2026-08-02

## 1. 目标

把 SPEC-0018 的 Normalized Evidence Contract 落成最小、纯本地、无 IO 的标准库类型 scaffold，
作为未来 Adapter、persistence 与 dedup 之前的安全类型边界。

这不是正式 normalization pipeline、Adapter、DB schema、collection 或 AI。

## 2. 前置条件

- SPEC-0018 Docs Review 已 PASS 并 Completed。
- SPEC-0017 只证明 replay candidate coverage，不提供 runtime mapping 授权。
- SPEC-0006 raw capture 继续 local-only、gitignored。
- SPEC-0005 X Source 范围不变。

## 3. 允许范围

- `market_intelligence.evidence` 小型包。
- 标准库 `StrEnum`、frozen/slots dataclasses。
- Common Evidence Envelope 纯数据合同。
- Provider item type 到 evidence kind/flags 的静态映射。
- IO-free、content-free、fail-closed validation helpers。
- 完全 mock-only 单元测试。

## 4. 明确非范围

- Provider Adapter、AdapterRegistry 或 raw Provider mapping。
- 读取 `local_evaluation/`、raw capture 或任何文件。
- API client、网络、capture、collection runner、scheduler。
- SQLAlchemy、DB session、migration、schema、ORM 或 persistence。
- 正式 parser、normalization、canonicalization、dedup、clustering 或 Event。
- AI client、Market Validation、投资建议、Telegram。

## 5. Enum contracts

### FR-01 Provider identity

- Provider：Marketaux、Finnhub、EIA、SEC EDGAR。
- Provider item type：`marketaux_news`、`finnhub_quote`、`eia_energy_timeseries`、
  `sec_filing`。
- Evidence kind：news、market data、energy official、disclosure。
- Source type：news、market data、official energy、disclosure。

### FR-02 Access and processing

- Access level：public fulltext、public summary、subscription required、licensed、link only、
  blocked、unknown。
- Processing status：pending、validated、blocked、invalid。
- `unknown` access level 必须产生安全 validation error，不得默认扩大访问范围。

## 6. Dataclass contracts

### FR-03 Presence types

- `ContentPresence` 只保存 title/body/URL/snippet/description 的存在性 booleans，不保存内容。
- `NumericPresence` 只保存 numeric presence、field count 与 nullable policy，不保存数值。
- `EvidenceError` 只包含安全 code、field 与 allowlisted safe message。
- `EvidenceFlags` 保存 official source、market data、disclosure、news signal booleans。

### FR-04 CommonEvidenceEnvelope

实现 SPEC-0018 logical fields：version、Provider/type/source/access、priority、item ID/hash、安全
reference、observed/event time、entity/asset/topic refs、dedup candidate、kind/confidence、presence、
flags、raw reference、processing status 与 safe errors。

限制：

- 不包含 title、body、URL、snippet、description 或 raw payload 字段；
- 不包含 quote/EIA/SEC 原始数值字段；
- `raw_payload_reference` 只是 opaque internal reference，不解析、不读取；
- dataclass 不是 ORM 或数据库 schema。

## 7. Pure mappings

| Provider item type | Evidence kind | Required flags |
|---|---|---|
| Marketaux news | news | news signal |
| Finnhub quote | market data | market data |
| EIA energy timeseries | energy official | official source |
| SEC filing | disclosure | official source + disclosure |

Unknown item type 必须 fail closed，使用固定安全错误，不回显输入。

## 8. Pure validation rules

### FR-05 Envelope validation

- unknown Provider/item type/evidence kind/source type/processing status fail closed；
- Provider、item type、evidence kind、source type 与 flags 必须一致；
- access level unknown 产生 `access_level_unknown`；
- provider item hash 必须存在且是 64 位小写 hex；不执行 dedup；
- event time 允许 null，但返回 `event_time_missing`，不得从 observed time 猜测；
- entity/asset/topic refs 允许为空；
- Marketaux entities/keywords 不作为必填；
- EIA `numeric_presence.nullable_allowed` 必须为 true；
- numeric field count 不得为负；
- embedded errors 不安全时只返回通用错误，不回显内容。

### FR-06 Raw payload reference validation

- `None` 合法；
- 非空 reference 只允许 `capture://`、`internal://`、`local-ref://` opaque prefixes；
- 外部 HTTP(S) URL fail closed；
- 包含 `api_key=`、`api_token=`、`token=`、authorization 或 `x-finnhub-token` fail closed；
- error 只返回固定 code/message，不包含原 reference 或 secret。

## 9. 安全属性

- 模块不导入网络、filesystem、database 或 AI client。
- 模块不读取环境变量、文件、raw capture 或 local evaluation data。
- validator 不修改输入、不执行 IO、不记录日志。
- 测试只使用 synthetic identifiers、hash 与 presence flags。

## 10. 测试要求

- 四 Provider item type mapping 与 flags。
- entity/asset/topic refs 空值合法。
- EIA nullable numeric 合法。
- unknown Provider/item type/evidence kind fail closed。
- unknown access level 返回安全 blocked/error。
- raw reference secret markers 与 external URL fail closed。
- 缺 event time 与 invalid hash 返回安全错误。
- unsafe embedded error 不回显输入。
- 源码无 network/DB/AI/local capture/file IO 依赖。
- dataclass 不包含真实内容、数值或 raw payload 字段。

## 11. 数据模型与迁移

- DB/schema/ORM 变化：无。
- Migration：无。
- 这些 dataclasses 仅为 runtime contract scaffold，不是 persistence entity。

## 12. 接口与任务变化

- 新增 Python import surface：`market_intelligence.evidence`。
- 无 API endpoint、CLI、Celery task、scheduler、collector 或 Adapter registration。

## 13. 错误处理

所有 validation errors 使用 snake_case safe code 与固定 safe message。未知或不安全输入不得
出现在错误、repr、日志或测试输出中。

## 14. 验收标准

- [x] Enums/dataclasses/mappings/validators 符合本 SPEC。
- [x] 所有实现是 pure、IO-free、Pydantic-free。
- [x] Secret/raw/content safety tests PASS。
- [x] 无 Adapter、DB、collection、正式 normalization/dedup/Event/AI。
- [x] Foundation、Ruff、mypy、pytest、package review PASS。

## 15. Verification Evidence

| Requirement | Evidence | Result |
|---|---|---|
| Pure scaffold | source import audit + unit tests | PASS |
| Content/secret safety | `tests/test_evidence_contracts.py` | PASS — 20 focused tests |
| Foundation | `python3 scripts/validate-foundation.py` | PASS |
| Quality | Ruff / mypy / pytest | PASS — 141 tests |
| Package safety | `scripts/package-review.sh /tmp/news_collect_spec0019_review.zip` | PASS |

## 16. 回滚

删除 `market_intelligence.evidence`、对应 tests 与本 SPEC；无 DB、migration 或外部数据回滚。

## 17. 后续候选（非 Active）

- SPEC-0020 — Provider Adapter Mapping to Evidence；
- SPEC-0021 — Evidence Persistence / DB Schema；
- SPEC-0022 — Dedup and Event Candidate Layer。

这些均未激活，不得由本 scaffold 顺带实现。

## 18. Review History

| Round | Result | Findings | Resolution |
|---|---|---|---|
| 1 | PASS | Pure contract scaffold approved | Completed — Implementation Review approved |

## 19. 架构治理检查

- Foundation：v2.1-FROZEN，未修改。
- Status：Completed — Implementation Review approved。
- SPEC-0005 X Source 范围不变；SPEC-0006/0017/0018 Completed。
- 无 Adapter、DB、migration、schema/ORM、collection、正式 normalization/dedup、Event 或 AI。
- 本 SPEC 只完成 pure evidence contract scaffold；未请求 API、读取 raw capture 或实现任何后续流程。
- Evidence Contract Scaffold 不是正式 Evidence Entity、Event Evidence Layer 或 AI Evidence Analysis。
