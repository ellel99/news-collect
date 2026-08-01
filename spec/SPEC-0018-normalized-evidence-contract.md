# SPEC-0018 — Normalized Evidence Contract

状态：Completed — Docs Review approved
阶段：Phase 1 — Contract Design
负责人：Project Owner
创建日期：2026-08-02
最后更新：2026-08-02

## 1. 目标

基于 SPEC-0017 已审核的四 Provider replay candidate coverage，定义下一阶段正式实现前的
Normalized Evidence Contract。

本 SPEC 只设计合同，不实现代码、数据库 schema、Adapter、collection 或正式 normalization。

## 2. 背景与依据

SPEC-0017 证明 19 个本地 items 可以生成 content-free candidate summaries，同时保留新闻、
行情、官方能源时间序列与公司披露的语义差异。主要依据：

- provider item ID、event time 与 dedup candidate coverage 均为 19/19；
- entity coverage 为 18/19，不能设为全局必填；
- EIA numeric value presence 为 4/5，numeric field 必须 nullable；
- Marketaux entities/keywords 覆盖不完整，不能强依赖；
- Finnhub 是 market data evidence，不是新闻；
- SEC EDGAR 与 EIA 是 official evidence，但不包含正文解析或分析。

## 3. Foundation 与 SPEC 边界

- Foundation v2.1-FROZEN 继续生效。
- SPEC-0005 保持 `Approved X Source and Account Collection` Planned 范围。
- SPEC-0006 保持 Completed，raw capture 继续 local-only、gitignored。
- SPEC-0017 保持 Completed，只证明 candidate coverage，不是正式 pipeline。
- 本文出现的字段是逻辑合同候选，不是数据库字段或 ORM 类型。
- 文档 Review PASS 也不授权实现；实现必须由独立 Active SPEC 明确批准。

## 4. 范围

本 SPEC 只定义两层逻辑合同：

1. Common Evidence Envelope；
2. Provider-specific Evidence Payload Contract。

同时定义字段语义、nullable 原则、Provider 差异、安全边界、失败行为和后续实现门禁。

## 5. 明确非范围

- 不定义数据库表、column、index、constraint 或 migration。
- 不写 ORM、Pydantic model 或 runtime validation code。
- 不写 Adapter、AdapterRegistry、正式 parser、collection runner 或 scheduler。
- 不写正式 normalization、canonicalization、dedup、clustering 或 Event。
- 不下载 article page、filing body 或 primary document。
- 不写 AI、Market Validation、投资建议、交易动作或 Telegram。
- 不请求外部 API，不运行 capture，不读取或提交 local raw payload。

## 6. 两层合同原则

Common Evidence Envelope 只承载跨 Provider 都可解释的 identity、time、reference、presence、
classification 与 processing metadata。Provider-specific payload 保留来源语义，不强行把行情、
能源时间序列或披露转换为新闻。

下游不得直接依赖 Provider SDK 或 raw payload；但这一原则的 runtime 实现不属于本 SPEC。

## 7. 第一层：Common Evidence Envelope

### 7.1 字段定义

| Field | Contract meaning | Required design |
|---|---|---|
| `evidence_version` | 逻辑合同版本 | required |
| `provider` | Provider identity | required |
| `provider_item_type` | Provider-specific evidence type | required |
| `source_type` | news / market data / official energy / disclosure 等来源类别 | required |
| `source_priority` | 经配置确认的来源优先级引用 | nullable；本 SPEC 不定评分算法 |
| `access_level` | 内容访问级别 | required；未知时 fail closed |
| `provider_item_id` | Provider stable item ID | required when contract verifies availability |
| `provider_item_hash` | 确定性 item hash | required；不是正式 dedup 结果 |
| `canonical_source_reference` | 可安全解析的来源引用 | nullable；不实现 canonicalization |
| `observed_at` | 系统观察时间 | required |
| `event_time` | Provider event/published/filing/period time | nullable |
| `entity_refs` | 标准化前实体引用候选 | nullable list |
| `asset_refs` | symbol/company/asset 引用候选 | nullable list |
| `topic_refs` | topic/sector/keyword 引用候选 | nullable list |
| `dedup_candidate_key` | Provider-scope deterministic candidate | nullable；不执行 dedup |
| `evidence_kind` | news / market_data / energy_official / disclosure | required |
| `evidence_confidence` | 对来源与结构证据的合同状态 | nullable；不代表 AI 置信度 |
| `content_presence` | 文本字段存在性摘要 | required boolean/structured presence |
| `numeric_presence` | 数值字段存在性摘要 | required boolean；值可 nullable |
| `official_source_flag` | 官方来源标记 | required |
| `market_data_flag` | 行情证据标记 | required |
| `disclosure_flag` | 披露证据标记 | required |
| `news_signal_flag` | 新闻线索标记 | required |
| `raw_payload_reference` | 内部安全 raw reference | nullable；不得包含 raw payload 或 secret |
| `processing_status` | pending/validated/blocked 等处理状态 | required；枚举待实现 SPEC 确认 |
| `errors` | 可审计的安全错误代码 | required list |

### 7.2 语义限制

- 本表不是数据库 schema，不要求现在确定 SQL/Python 类型。
- `provider_item_hash` 与 `dedup_candidate_key` 不等同于 canonical identity 或 cross-provider dedup。
- `canonical_source_reference` 只是合同位置，不授权 URL canonicalization 或访问 source page。
- `raw_payload_reference` 只能是本地/内部安全引用；不得嵌入 raw payload、credential、secret URL
  或受限正文。
- `evidence_confidence` 只能表达合同/来源证据状态，不得伪装成 AI 判断或投资确信度。
- `entity_refs`、`asset_refs`、`topic_refs` 必须允许 nullable/empty，不得因缺失而编造。

## 8. 第二层：Provider-specific Evidence Payload Contract

### 8.1 Marketaux News Evidence

用途：新闻线索，可在未来进入 content/evidence pipeline；不直接生成投资建议。

候选来源字段：uuid、title、URL、snippet、description、published time、source、entities、keywords、
language。

合同关注点：

- `content_text_present`、`source_present`、`entity_present`、`timestamp_present`、
  `dedup_key_present`；
- uuid 是首选 provider item ID candidate；
- entities/keywords 不可强依赖，必须允许缺失；
- content license、retention 与内部 AI 使用权仍须后续合同核验；
- 不在文档、日志或 review output 中输出真实内容或 URL。

### 8.2 Finnhub Market Data Evidence

用途：市场价格验证证据，未来可供独立 Market Validation SPEC 使用；不是新闻源，不直接产生
交易或投资建议。

候选来源字段：symbol、`c/d/dp/h/l/o/pc`、`t`。

合同关注点：

- `market_data_flag=true`、`numeric_presence=true`；
- `event_time` 候选来自 `t`，`asset_ref` 候选来自 symbol；
- quote field values 不进入文档或 content-free review output；
- 本 SPEC 不实现 Market Validation、行情计算、阈值判断或信号生成。

### 8.3 EIA Energy Official Evidence

用途：官方能源时间序列证据，可在未来用于能源主题影响验证；不是新闻源。

候选来源字段：period、price/value、sector ID/name、state ID/description、unit fields。

合同关注点：

- `official_source_flag=true`、`energy_evidence_flag=true`；
- numeric presence/value 必须允许 nullable；缺失值不得猜测或补零；
- geography/sector 可形成 entity/topic reference candidates；
- 本 SPEC 不执行时间序列分析、趋势判断、影响分析或单位转换。

### 8.4 SEC EDGAR Disclosure Evidence

用途：官方公司披露证据，可在未来用于公司披露 evidence；不是新闻源。

候选来源字段：accession number、form、filing date、acceptance time、report date、ticker、
primary document reference。

合同关注点：

- `official_source_flag=true`、`disclosure_flag=true`；
- provider item ID candidate 来自 accession number；
- event time candidate 优先级须由实现 SPEC 在 filing date / acceptance time 间明确；
- primary document 只能作为安全 reference candidate，不下载或解析正文；
- 本 SPEC 不构造 Event、不判断披露重要性、不生成投资建议。

## 9. Provider type 与 evidence kind mapping

| Provider item type | Evidence kind | Key flags |
|---|---|---|
| `marketaux_news` | `news` | `news_signal_flag=true` |
| `finnhub_quote` | `market_data` | `market_data_flag=true` |
| `eia_energy_timeseries` | `energy_official` | `official_source_flag=true` |
| `sec_filing` | `disclosure` | `official_source_flag=true`, `disclosure_flag=true` |

Mapping 只是合同提案；不得据此注册 Adapter 或创建 persistence entity。

## 10. Nullable 与 fail-closed 原则

- replay 未证明全覆盖的字段必须 nullable，尤其是 Marketaux entities/keywords 与 EIA numeric。
- Provider item ID/event time 的来源字段缺失时必须产生明确 processing error，不得合成虚假值。
- access/license/retention 未确认时，content retention 和 downstream use 必须 blocked。
- 未知 Provider、未知 item type、secret risk、unsafe raw reference 必须 fail closed。
- 错误必须使用安全 code，不得包含 raw value、secret、完整 URL 或正文。

## 11. 时间语义

- `observed_at` 是系统观察时间，不得替代 Provider event time。
- Marketaux event time candidate 来自 published time。
- Finnhub event time candidate 来自 quote timestamp。
- EIA event time candidate 来自 period，并保留 period precision。
- SEC event time 的 filing/acceptance/report 语义不同；实现前必须明确优先级与保留策略。
- 缺失 event time 保持 null，不根据 observed time 猜测。

## 12. Identity 与 dedup 边界

- Marketaux 首选 uuid；Finnhub 候选可组合 provider/symbol/timestamp；EIA 候选可组合
  provider/period/geography/sector；SEC 首选 accession number。
- 上述只定义 provider-scope candidate，不执行 cross-provider identity、URL canonicalization 或
  semantic dedup。
- 正式 dedup 必须等待独立 SPEC，且不得直接依赖 raw Provider payload。

## 13. Access、license 与 retention

- `access_level` 必须沿用已审核来源合同；未知时不能扩大保存范围。
- Marketaux content license/retention/internal analysis 权限仍需实现前确认。
- Finnhub/EIA 数值与 SEC metadata 的保存期限、再分发与 attribution 必须由 Provider 合同确认。
- 本 SPEC 不改变任何 Provider 权利，不把结构可见性解释为长期保存或 AI 使用授权。

## 14. Processing status 候选

可讨论的逻辑状态包括 `pending`、`validated`、`blocked`、`invalid`。这些不是已批准 enum，也
不是 schema 事实。后续实现 SPEC 必须定义转换、错误映射、幂等与审计行为。

## 15. 安全与输出合同

- 文档与测试 evidence 只能输出 counts、booleans、field names、hash 和安全 error codes。
- 禁止输出/提交 title、body、URL、snippet、description、quote/EIA/filing value。
- 禁止提交 raw capture、`.env`、key/token 或 `local_evaluation/`。
- 禁止把 raw payload reference 解析为正文或自动 follow external link。

## 16. 验收标准（Docs Review）

- [x] 两层合同的职责与边界明确。
- [x] 四 Provider payload contract 保留语义差异。
- [x] nullable 与 fail-closed 规则覆盖 SPEC-0017 findings。
- [x] 明确没有数据库、Adapter、collection、正式 normalization/dedup/Event/AI 实现。
- [x] 后续候选 SPEC 未激活。
- [x] Foundation validator 与 docs/package checks PASS。

## 17. Verification Evidence

| Requirement | Evidence | Result |
|---|---|---|
| Docs-only scope | Git diff file allowlist | PASS — Markdown only |
| No API/capture/local data | delivery declaration and Git tracking check | PASS |
| Foundation | `python3 scripts/validate-foundation.py` | PASS |
| Repository regression | Ruff format/check and pytest | PASS — 121 tests |
| Package safety | `scripts/package-review.sh /tmp/news_collect_spec0018_review.zip` | PASS |

## 18. 后续工作

- SPEC-0019 — Evidence Contract Implementation Scaffold，由独立 Active SPEC 承接；
- SPEC-0020 — Provider Adapter Mapping to Evidence；
- SPEC-0021 — Evidence Persistence / DB Schema；
- SPEC-0022 — Dedup and Event Candidate Layer。

SPEC-0020–0022 仍只是候选规划。必须逐一完成文档准备、Review 与用户明确授权，且任何
Event/AI/Market Validation 范围仍受 Foundation revision/Freeze Review 约束。

## 19. Review History

| Round | Result | Findings | Resolution |
|---|---|---|---|
| 1 | PASS | Two-layer normalized evidence contract | Approved and merged in PR #17 |

SPEC-0018 Completed 只表示合同设计通过。它不授权直接实现 Adapter、DB/schema、collection、
正式 normalization、dedup、Event 或 AI；最小纯代码合同边界由 SPEC-0019 单独承接。

## 20. 回滚

删除本 SPEC，并将 Active SPEC 恢复为 `None` 或用户明确指定的已审核 SPEC。无代码、数据库或
外部数据回滚。

## 21. 架构治理检查

- Foundation：v2.1-FROZEN，未修改。
- Phase：Phase 1 Contract Design。
- Completion state：SPEC-0018 Completed；实现授权仅来自后续独立 Active SPEC。
- SPEC-0005 X Source 范围不变；SPEC-0006/0017 Completed。
- 无 Adapter、DB、migration、schema/ORM、collection、正式 normalization/dedup、Event 或 AI。
