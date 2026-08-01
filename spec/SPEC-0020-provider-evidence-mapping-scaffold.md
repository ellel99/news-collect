# SPEC-0020 — Provider Evidence Mapping Scaffold

Status：Active — Implementation Review

Phase：Phase 1 — Provider Mapping Scaffold

Foundation：v2.1-FROZEN（unchanged）

Depends on：SPEC-0019（Completed）

## 1. 目标

基于 SPEC-0019 `CommonEvidenceEnvelope`，为 Marketaux、Finnhub、EIA Open Data 与 SEC EDGAR
实现 pure in-memory mapping scaffold。映射只产生枚举、布尔 presence、计数、时间、哈希和不透明
引用，不传播 provider content 或数值。

## 2. 非目标

本 SPEC 不是 Provider Adapter、AdapterRegistry、collection、scheduler、DB、migration、schema、
ORM、persistence、正式 normalization pipeline、dedup、clustering、Event、AI、投资建议或 Telegram。
不得请求 API、执行 capture、读取 `.env`、读取 `local_evaluation/` 或真实 raw capture。

## 3. 接口

`market_intelligence.evidence.provider_mappings` 提供：

- `map_marketaux_news_to_evidence(item, context)`；
- `map_finnhub_quote_to_evidence(item, context)`；
- `map_eia_energy_row_to_evidence(item, context)`；
- `map_sec_filing_to_evidence(item, context)`。

输入仅为调用者已经持有的 `Mapping[str, object]`。`context.observed_at` 必须显式提供，mapper
不得读取时钟或猜测观察时间。

## 4. 共同安全合同

- provider item ID 转换为 source-scoped opaque hash reference；不输出 uuid、accessionNumber 或 symbol。
- `provider_item_hash` 是确定性的 64 位 lowercase SHA-256。
- `raw_payload_reference` 只使用 `internal://` opaque reference，不包含 URL、secret 或 provider ID。
- `access_level=link_only`；不得推断全文授权。
- content/value 只转换为 presence/count，不进入 envelope。
- 缺 provider item ID 时返回 `blocked` 与固定安全错误；缺 event time 时保持 `None`，由 validator
  返回 `event_time_missing`，不得猜测。

## 5. Provider 映射

### Marketaux

`uuid` 只作为 opaque ID 的哈希输入；`published_at` 映射 event time；title、URL、snippet、
description 只映射 presence；entities/keywords 只生成哈希候选 refs；`news_signal_flag=true`。

### Finnhub

context symbol 只生成安全 asset ref；`t` 映射 event time；`c/d/dp/h/l/o/pc` 只统计数值字段
数量与 presence，不保存值；`market_data_flag=true`。不实现 Market Validation。

### EIA

`period` 映射 event time；price/value 只统计 presence/count，允许 nullable；geography/sector 只生成
安全哈希 refs；`official_source_flag=true`。不做时间序列分析。

### SEC EDGAR

accessionNumber 只作为 opaque ID 哈希输入；event time 优先级为 acceptanceDateTime、filingDate、
reportDate；ticker/form 只生成安全哈希 refs；primaryDocument 只映射 URL presence，不保存原值；
`official_source_flag=true`、`disclosure_flag=true`。不下载或解析 filing body。

## 6. 测试与验收

- 四 mapper mock-only tests 覆盖类型、flags、presence/count 与 validator 兼容性。
- EIA 缺数值合法；SEC event time 优先级固定。
- missing ID fail closed；missing event time 不推断。
- 序列化 envelope 不含真实 title/body/URL/snippet/description、quote/EIA value、accessionNumber 或
  primaryDocument。
- source audit 证明无 network、filesystem、DB、AI、local capture 依赖。
- Foundation、Ruff、mypy、pytest、package review 全部 PASS。

## 7. 数据与迁移

无 DB/schema/ORM 变化，无 Alembic revision，无 persistence。

## 8. Verification Evidence

| Requirement | Evidence | Result |
|---|---|---|
| Pure mapping | source audit + mock-only mapping tests | PASS — 9 focused tests |
| Full regression | `.venv/bin/pytest` | PASS — 150 tests |
| Static quality | Ruff check/format + mypy | PASS |
| Foundation | `python3 scripts/validate-foundation.py` | PASS |
| Package safety | `scripts/package-review.sh /tmp/news_collect_spec0020_review.zip` | PASS |

未使用真实 provider/capture 作为测试夹具，未请求 API 或读取 `local_evaluation/`。

## 9. 后续候选（非 Active）

SPEC-0021 Evidence Persistence / DB Schema 与 SPEC-0022 Dedup and Event Candidate Layer 均未激活。
SPEC-0005 X Source and Account Collection 范围保持不变。
