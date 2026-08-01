# SPEC-0017 — Four Provider Replay Normalization Candidate

状态：Completed — local replay-only verification approved
阶段：Phase 1 — Local Evaluation Tooling
负责人：Project Owner
创建日期：2026-08-02
最后更新：2026-08-02

## 1. 目标

只读取 `local_evaluation/raw_provider_captures/` 中已通过 audit 的 Marketaux、Finnhub、EIA
Open Data 与 SEC EDGAR 本地 capture，生成 content-free normalization candidate summary。

本 SPEC 用于评估字段覆盖与两层候选合同，不是正式 normalization pipeline。

## 2. 背景

SPEC-0006 已完成四 Provider 最小 raw capture、安全 audit 与 replay readiness 验证。Raw captures
不得进入 Git、PR 或 chat，但本地 replay 可以在不重复请求 Provider 的前提下评估确定性映射。
四个平台的语义不同，不能强制映射为单一新闻对象，因此本 SPEC 使用：

1. common normalized envelope candidate；
2. provider-specific evidence summary。

## 3. Foundation 与 SPEC 边界

- Foundation v2.1-FROZEN 保持生效。
- SPEC-0006 raw capture、安全与本地保留边界保持不变。
- SPEC-0005 继续是 `Approved X Source and Account Collection` Planned，不被本 SPEC 改写。
- 不实现正式 Adapter、AdapterRegistry、collection、scheduler、DB、migration、schema 或 ORM。
- 不实现正式 normalization、canonicalization、cross-provider dedup、clustering 或 scoring。
- 不实现 AI、Event、Market Validation、投资建议或 Telegram。

## 4. 前置条件

- Marketaux：3 items，audit/replay PASS。
- Finnhub：1 item，audit/replay PASS。
- EIA Open Data：5 items，secret hardening 后 audit/replay PASS。
- SEC EDGAR：10 items，audit/replay PASS。
- 本轮不执行 `provider_capture.py --execute`，不请求外部 API。

## 5. 范围

- 新增 `scripts/provider_normalize_replay.py`。
- 支持单 capture 文件模式与四 Provider 目录汇总模式。
- 输出字段存在性、counts、booleans、coverage 与稳定 SHA-256 hash。
- 输出 common envelope candidates 与 provider-specific summaries。
- 新增完全 mock-only 的安全、路径、结构与聚合测试。

## 6. 非范围

- 真实 API 请求、capture、分页、backfill、网页或 filing 下载。
- 输出或提交 raw response、title、body、URL、snippet、description、quote/EIA/filing value。
- Source/RawItem/ContentItem persistence 或任何数据库操作。
- Provider Adapter、正式 parser、正式 pipeline 或生产任务。
- AI API、分析、投资信号、建议或交易动作。

## 7. 输入与安全合同

### FR-01 Local-only input

- 单文件只能位于 `local_evaluation/raw_provider_captures/` 的直接子级。
- `--capture-dir` 必须精确指向该目录。
- 路径越界、invalid JSON、非 object capture、unknown provider、空 items、空目录必须 fail closed。
- 目录模式要求四个 Provider 均存在；缺失 Provider 必须 fail closed。
- 输入中检测到 secret-named field 或 secret query marker 时必须 fail closed。
- 脚本不得导入网络 client、数据库 client 或 AI client。

### FR-02 Content-free output

- 不输出任何真实 title/body/URL/snippet/description。
- 不输出 quote value、EIA value、accession number、primary document 或 filing value。
- 不输出 key/token/`.env`、raw request 或 raw response。
- `provider_item_hash` 与 `source_capture_hash` 使用 SHA-256；不输出被 hash 的原值。
- `content_values_emitted` 必须恒为 `false`。

## 8. Common normalized envelope candidate

每个输入 item 生成一个安全候选记录：

- `normalized_candidate_version`；
- provider 与 `provider_item_type`；
- `source_capture_hash`、`provider_item_hash`；
- provider item ID、event/observed time、source、entity、asset/company、dedup key 的可用性；
- content text、numeric value 的可用性；
- official source、market data、disclosure、news signal flags；
- content-free errors。

Provider item type 固定为：

| Provider | Type |
|---|---|
| Marketaux | `marketaux_news` |
| Finnhub | `finnhub_quote` |
| EIA | `eia_energy_timeseries` |
| SEC EDGAR | `sec_filing` |

这些 booleans/hash 只是候选覆盖证据，不是正式统一数据模型、canonicalization 或 dedup 结果。

## 9. Provider-specific evidence summary

### FR-03 Marketaux

统计 uuid、title、URL、published time、snippet、description、source、entities、keywords、language、
dedup key 与 timestamp availability。只输出 counts。

### FR-04 Finnhub

统计 symbol、quote timestamp、价格候选字段 `c/d/dp/h/l/o/pc` 的 coverage 与 numeric field count；
`market_data_flag=true`。不输出任何 quote 数值，不执行 Market Validation。

### FR-05 EIA Open Data

统计 period、geography、sector 与 numeric value field availability；
`official_source_flag=true`、`energy_evidence_flag=true`。不输出数值或计算时间序列。

### FR-06 SEC EDGAR

统计 accession、form、filing date、acceptance time 与 ticker availability；
`official_source_flag=true`、`disclosure_flag=true`。不输出 accession/primaryDocument，不下载 filing。

## 10. CLI

单文件模式：

```text
.venv/bin/python scripts/provider_normalize_replay.py <capture_file>
```

目录模式：

```text
.venv/bin/python scripts/provider_normalize_replay.py \
  --capture-dir local_evaluation/raw_provider_captures
```

两个输入不能同时使用，也不能同时缺失。

## 11. 目录汇总合同

目录输出包括：candidate version、capture/provider counts、total input/candidate items、
provider-specific summaries、common envelope coverage、provider type counts、
`content_values_emitted=false` 与 errors。

目录模式不输出 per-item 原始值；聚合四 Provider 时保留各自 evidence summary 字段差异。

## 12. 错误处理

| Error | 条件 | Exit |
|---|---|---|
| `select_exactly_one_input` | 同时选择或未选择输入模式 | 2 |
| `capture_path_outside_local_evaluation` | 文件/目录越界 | 2 |
| `invalid_capture` / `capture_must_be_object` | 无法读取/解析 capture | 2 |
| `secret_risk_detected` | capture 有 secret risk | 2 |
| `unknown_provider` | Provider 不在 allowlist | 2 |
| `no_input_items` / `no_captures_found` | 输入为空 | 2 |
| `missing_required_providers` | 目录模式不含全部四 Provider | 2 |

错误输出只能包含安全 error code，不包含路径内容、异常详情或输入值。

## 13. 数据模型与迁移

- 数据库变化：无。
- Migration：无。
- Schema/ORM：无。
- 本地 candidate summary 不是产品数据模型，也不写入数据库。

## 14. 测试要求

- 四 Provider mock capture 分别产生 content-free candidate summary。
- 目录模式聚合四 Provider，并验证 3/1/5/10 共 19 个 mock items。
- 输出不包含新闻内容、URL、quote/EIA/SEC value 或 secret。
- unknown provider、invalid JSON、空 items、空目录、缺 Provider、路径越界 fail closed。
- 脚本不导入 `httpx`、`requests`、`urlopen`、SQLAlchemy 或 AI client。
- 测试不读取真实 `local_evaluation/`，不访问网络。

## 15. 验收标准

- [x] 单文件与四 Provider 目录模式符合合同。
- [x] common envelope 只含 safe metadata/booleans/hash/errors。
- [x] provider-specific summary 保留四类语义差异。
- [x] 所有安全与 fail-closed 测试 PASS。
- [x] 无真实 API、capture、raw output、Adapter、DB、collection 或 AI。
- [x] Foundation、Ruff、mypy、pytest、package review PASS。

## 16. Verification Evidence

| Requirement | Evidence | Result |
|---|---|---|
| No external API/capture | mock-only delivery declaration | PASS — zero requests |
| Content-free output | `tests/test_provider_normalize_replay.py` | PASS |
| Foundation | `python3 scripts/validate-foundation.py` | PASS |
| Quality | Ruff / mypy / pytest | PASS — 121 tests |
| Package safety | `scripts/package-review.sh /tmp/news_collect_spec0017_review.zip` | PASS |

### 16.1 Local replay-only verification summary

本地真实 replay 只读取 gitignored captures，输出 content-free summary；没有任何 raw content、
secret 或真实字段值进入 Git、PR 或 chat。

| Metric | Result |
|---|---:|
| capture files seen | 4 |
| providers seen | Marketaux, Finnhub, EIA, SEC EDGAR |
| total input items | 19 |
| total candidate items | 19 |
| content values emitted | false |
| errors | none |

Provider type counts：

| Type | Count |
|---|---:|
| `marketaux_news` | 3 |
| `finnhub_quote` | 1 |
| `eia_energy_timeseries` | 5 |
| `sec_filing` | 10 |

Common envelope coverage：

| Coverage | Count |
|---|---:|
| provider item ID available | 19 / 19 |
| event time available | 19 / 19 |
| dedup key available | 19 / 19 |
| entity available | 18 / 19 |
| official source | 15 / 19 |
| market data | 1 / 19 |
| news signal | 3 / 19 |
| disclosure | 10 / 19 |

## 17. 回滚

删除本 SPEC、replay normalization candidate 脚本与对应 mock tests。无数据库或外部数据回滚。

## 18. 已知限制

- Candidate hashes 不代表正式 dedup key 或跨 Provider identity。
- 字段存在不代表字段值正确、许可允许长期保留或可用于正式分析。
- `content_text_available`/`numeric_value_available` 只表达存在性，不输出或分析值。
- 目录模式是本地 evaluation，不是 batch job、scheduler 或 collection runner。

### 18.1 Provider findings

- Marketaux：3/3 candidates；uuid、title、URL、published time、snippet、description、source、
  language 均为 3/3，entities 为 2/3，keywords 为 1/3。可作为后续 news evidence mapping
  输入，但 entities/keywords 不得成为必填依赖。
- Finnhub：1/1 candidate；symbol、quote timestamp 与 `c/d/dp/h/l/o/pc` coverage 均为 1/1。
  只能作为 market data evidence，不是新闻源，不直接产生投资建议。
- EIA：5/5 candidates；period、geography、sector 均为 5/5，numeric value field 为 4/5。
  官方能源时间序列合同必须允许 numeric presence/value nullable。
- SEC EDGAR：10/10 candidates；accession、form、filing date、acceptance time、ticker 均为
  10/10。披露 metadata 结构完整，但 capture 不包含 filing body。

SPEC-0017 只证明 normalization candidate coverage 与安全摘要可行；它不是正式 normalization
pipeline，不定义持久化、canonicalization、dedup、Event 或 AI 行为。

## 19. Review History

| Round | Result | Findings | Resolution |
|---|---|---|---|
| 1 | PASS | SPEC/scaffold and mock-only tests | Merged in PR #16 |
| 2 | PASS | Four-provider local replay-only summary | 19/19 candidates; content-free output approved |

## 20. 架构治理检查

- Foundation：v2.1-FROZEN，未修改。
- Completion state：SPEC-0017 Completed；下一 Active SPEC 由索引决定。
- SPEC-0005 X Source 范围未修改。
- SPEC-0006 capture 边界未扩大。
- 无未来阶段实体、AI、Event、Portfolio 或交易语义。
