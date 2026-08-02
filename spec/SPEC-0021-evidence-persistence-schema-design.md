# SPEC-0021 — Evidence Persistence / DB Schema Design

Status：Active — Docs Review

Phase：Phase 1 — Schema Design Only

Foundation：v2.1-FROZEN（unchanged）

Depends on：SPEC-0019、SPEC-0020（Completed）

## 1. 目标

基于 `CommonEvidenceEnvelope` 与四 Provider pure mapping scaffold，设计 evidence persistence 的
候选数据库边界。本 SPEC 只回答表、字段、关系、唯一性、许可和迁移门禁问题，不授权实现。

## 2. 严格非范围

- 不写 Python、Alembic migration、SQLAlchemy model、repository、service 或 persistence code。
- 不实现 Provider Adapter、AdapterRegistry、collection、scheduler 或正式 normalization pipeline。
- 不实现 cross-provider/semantic dedup、clustering、Event、AI、投资建议或 Telegram。
- 不请求 API、不执行 capture、不读取 `.env`、`local_evaluation/` 或真实 raw capture。
- 不改变 Foundation v2.1-FROZEN；本设计不是正式 Evidence Entity、Event Evidence Layer 或 AI
  Evidence Analysis 的实现授权。

## 3. 独立表决策候选

候选表名为 `evidence_items`，逻辑上独立于 `raw_items` 与 `content_items`：

- 独立表允许新闻、行情、能源和披露 metadata 使用同一 provenance/safety contract；
- `raw_item_id` 必须非空并引用 `raw_items.id`，每条 evidence 必须能追溯到已采集原始记录；
- `content_item_id` 可空并引用 `content_items.id`。Finnhub/EIA 等 non-content evidence 不要求
  ContentItem；Marketaux 新闻可在后续获批流程中关联 ContentItem；
- SEC filing metadata 只要求 `raw_item_id`，不得因 `content_item_id` 为空而下载或保存 filing body；
- 建表本身仍需后续独立实现授权，本节不是当前 schema 事实。

## 4. 候选字段合同

| 字段 | 候选类型 | Nullable | 设计约束 |
|---|---|---:|---|
| `id` | UUID | no | 主键 |
| `evidence_version` | integer | no | positive；对应 contract version |
| `provider` | varchar(64) | no | allowlisted provider code |
| `provider_item_type` | varchar(64) | no | 与 provider/evidence kind 一致 |
| `evidence_kind` | varchar(64) | no | allowlisted contract value |
| `source_type` | varchar(64) | no | 与 item type 一致 |
| `source_id` | UUID | no | FK `sources.id`；provenance required |
| `source_account_id` | UUID | yes | FK `source_accounts.id`；source-level provider 合法 |
| `raw_item_id` | UUID | no | FK `raw_items.id`；provenance required |
| `content_item_id` | UUID | yes | FK `content_items.id`；non-content provider 可空 |
| `provider_item_id` | varchar(255) | yes | 只允许 opaque、安全的 provider-scoped ID |
| `provider_item_hash` | char(64) | no | lowercase SHA-256 |
| `event_time` | timestamptz | yes | 缺失保持 null，不从 observed_at 推断 |
| `observed_at` | timestamptz | no | 调用方明确提供的观察时间 |
| `access_level` | varchar(64) | no | unknown/blocked downstream fail closed |
| `processing_status` | varchar(64) | no | allowlisted lifecycle state |
| `official_source_flag` | boolean | no | default false；不扩大授权 |
| `market_data_flag` | boolean | no | default false；不代表 Market Validation 已实现 |
| `disclosure_flag` | boolean | no | default false |
| `news_signal_flag` | boolean | no | default false |
| `content_presence` | jsonb | no | 固定 boolean keys；不得保存 content value |
| `numeric_presence` | jsonb | no | presence/count/nullable only；不得保存 numeric value |
| `entity_refs` | jsonb | no | opaque refs array；default empty array |
| `asset_refs` | jsonb | no | opaque refs array；default empty array |
| `topic_refs` | jsonb | no | opaque refs array；default empty array |
| `raw_payload_reference` | varchar(512) | yes | 仅 opaque internal reference；不得为 external URL |
| `errors` | jsonb | no | fixed safe code/field/message objects；default empty array |
| `created_at` | timestamptz | no | DB-managed creation time candidate |
| `updated_at` | timestamptz | no | DB-managed update time candidate |

`dedup_candidate_key` 可作为后续实现审核的 nullable candidate 字段，但在字段清单获批前不进入
migration；它只代表 provider scope candidate，不代表已去重。

## 5. 严格禁止持久化字段和值

`evidence_items` 不得包含或间接保存：

- title、body、URL、snippet、description；
- quote value、EIA value；
- accessionNumber 原文、primaryDocument 原文；
- raw payload、完整 response、网页 HTML 或 filing body；
- API key、token、Authorization value、`.env` 内容；
- 带 secret 的完整 external request URL。

JSONB 不是绕过禁字段的容器。字段 allowlist、shape validation 和 secret/content safety 必须在
后续实现 SPEC 中明确并测试。

## 6. 关系与一致性设计

- `source_id` 与 `raw_item_id` required；`raw_items.source_id` 必须与 evidence `source_id` 一致。
- `source_account_id` nullable；若存在，必须属于同一 `source_id`，并与 raw item provenance 一致。
- `content_item_id` nullable；若存在，必须能追溯至同一 raw/source provenance。
- Finnhub/EIA 可没有 ContentItem；不能因此伪造空 ContentItem。
- Marketaux 可在合法 retention/normalization 合同获批后关联 ContentItem。
- SEC metadata 默认只关联 RawItem；不得自动获取或持久化披露正文。
- provider-specific semantics 只通过 allowlisted enums、flags、presence/count 和 opaque refs 表达，
  不保存或暴露 provider raw payload。

具体 FK delete behavior、deferrability 与跨表一致性实现方式仍待 implementation review 决定。

## 7. 唯一性与去重边界

候选约束如下，必须在迁移授权前用 PostgreSQL 集成测试验证：

- `UNIQUE(provider, provider_item_hash)` 是首选幂等候选；
- `UNIQUE(provider, provider_item_id) WHERE provider_item_id IS NOT NULL` 是第二候选；
- 两者同时存在是否会错误折叠 provider revision，必须在实现前用 synthetic fixtures 评估；
- `dedup_candidate_key` 即使未来获批，也只用于 provider-scoped candidate lookup；
- 不做 cross-provider dedup、semantic dedup、clustering 或 Event generation。

本 Docs Review 不最终批准任何索引或唯一约束。

## 8. Retention、License 与 Access 门禁

- `access_level=unknown` 或 `blocked` 时，任何 downstream use 必须 blocked；不得以已有 row 推断授权。
- Marketaux content retention 与 internal AI use 仍需 provider contract 明确确认；本表不得保存内容值。
- Finnhub、EIA、SEC 的 numeric/metadata retention 受各自 provider contract 限制；presence/count
  schema 不能扩大保存、再分发或 AI 使用授权。
- flags 只表达结构性分类，不是 license、accuracy、official endorsement 或 downstream use grant。
- retention duration、删除策略与审计证据未批准前，implementation 必须 fail closed。

## 9. 候选索引与查询面

仅供 Docs Review：

- provenance：`raw_item_id`、`source_id`、`source_account_id`；
- provider lookup：`(provider, provider_item_hash)`、可空 `(provider, provider_item_id)`；
- time/status：`event_time`、`observed_at`、`processing_status`；
- optional relation：`content_item_id`。

不批准 JSONB GIN、semantic/vector index 或 Event-oriented index。所有索引需以真实查询计划、写入成本
和 retention policy 重新审核。

## 10. Migration Gate

- SPEC-0021 Docs Review PASS 不等于允许 migration、ORM 或 persistence implementation。
- Alembic migration 必须由后续 `SPEC-0022` 或独立 `SPEC-0021-implementation` 明确授权；编号由
  用户/Reviewer 最终确认，当前不得预先激活。
- DB 变更前，用户/ChatGPT 必须明确批准最终字段列表、类型、nullable/default、FK/delete behavior、
  checks、索引、唯一约束、数据安全测试和 downgrade/rollback 方案。
- 必须证明没有未来阶段实体、Event/Analysis/AI 字段，且现有九张 Phase 1 表不被越权修改。

## 11. 测试要求（未来实现门禁）

后续获批实现至少需要：migration upgrade/downgrade/re-upgrade、PostgreSQL FK/unique/check tests、
schema allowlist/denylist、content/value/secret rejection、nullable non-content evidence、provenance
一致性、unknown/blocked access fail-closed 与 rollback 验证。SQLite 不得代替 PostgreSQL 语义。

本轮不写或运行新增测试代码；只运行现有回归以证明 docs-only 变更未破坏仓库。

## 12. 验收标准

- [ ] 用户/ChatGPT 审核独立表、required/nullable relations 与禁字段边界。
- [ ] 用户/ChatGPT 审核候选唯一性、retention/license/access 门禁。
- [ ] 明确记录 Docs Review PASS 不授权 migration/ORM/persistence。
- [ ] 本 PR 只有文档变更，无 API、capture、Adapter、DB 或后续阶段实现。

## 13. Verification Evidence

由本 PR 的文档 diff、Foundation validator、现有回归测试和 package review 记录证明。没有读取或
提交 raw capture/`local_evaluation/`，也没有请求 Provider。

## 14. 已知问题 / 待 Reviewer 决策

- provider revision 是否应允许同一 provider item ID 对应多个 hashes；
- `dedup_candidate_key` 是否进入首版表；
- FK delete behavior 与一致性约束由 DB 还是 application enforcement；
- 各 Provider 最终 retention duration 与 license evidence；
- Docs Review 后采用 `SPEC-0021-implementation` 还是新的 SPEC 编号。
