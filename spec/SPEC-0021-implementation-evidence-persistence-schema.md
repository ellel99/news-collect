# SPEC-0021 — Evidence Persistence Schema Implementation

Status：Completed — Implementation Review approved

Phase：Phase 1 — Evidence Persistence Schema Implementation

Foundation：v2.1-FROZEN（unchanged）

Depends on：SPEC-0021 Evidence Persistence / DB Schema Design（Completed）

## 1. 目标

实现已批准的 `evidence_items` PostgreSQL 表、SQLAlchemy `EvidenceItem` 模型和 PostgreSQL
schema tests。实现只建立安全、可追溯的 evidence persistence schema，不接入任何生产流程。

## 2. 实现范围

- 新增单一 Alembic revision `0003`，只创建 `evidence_items`。
- 保留 `system_metadata` 与既有九张 Phase 1 业务表，不修改其字段或约束。
- 按已批准合同实现 UUID/FK、nullable、默认值、allowlist checks、flags consistency、JSONB shape、
  internal-only raw reference、唯一索引与查询索引。
- 使用 `raw_items(id, source_id)` composite unique index 与
  `evidence_items(raw_item_id, source_id)` composite FK 在 DB 层强制 provenance consistency。
- `raw_payload_reference` 除 internal-only scheme 外，还必须拒绝 api key、token、authorization 与
  Finnhub token marker，防止秘密被编码进 opaque reference。
- 新增 `EvidenceItem` ORM 及 Source、SourceAccount、RawItem、ContentItem 双向关系。
- 使用 PostgreSQL 语义验证 upgrade/downgrade/re-upgrade、schema、FK、checks 与 indexes。

## 3. 严格非范围

- 不请求 Provider API，不执行 capture，不读取或提交 `.env`、`local_evaluation/` 或 raw capture。
- 不实现 Provider Adapter、AdapterRegistry、repository、service、collection 或 scheduler。
- 不实现正式 normalization、dedup、clustering、Event、AI、投资建议或 Telegram。
- 不保存或输出真实 title/body/URL/snippet/description、行情/EIA 数值或 SEC filing value。
- 不启动 SPEC-0022；不修改 SPEC-0005 X Source and Account Collection 范围。

## 4. Schema Contract

唯一新增表为 `evidence_items`。字段、类型、nullable、defaults、FK、checks 和 indexes 必须与
已批准的 `spec/SPEC-0021-evidence-persistence-schema-design.md` 及本实现授权完全一致。禁止添加
content value、raw payload、secret、Event、Analysis、Recommendation、Portfolio 等字段。

## 5. Migration Contract

- `upgrade 0002 -> 0003` 只创建 `evidence_items` 及其约束和索引。
- `downgrade 0003 -> 0002` 只删除 `evidence_items`。
- re-upgrade 必须成功；不得改变既有表、enum 或 Foundation 状态。

## 6. 测试要求

- PostgreSQL migration upgrade、downgrade、re-upgrade。
- 表与必需字段存在，禁字段不存在；ORM metadata 与迁移字段一致。
- required `raw_item_id` FK 与 nullable `content_item_id`。
- Finnhub/EIA 风格 non-content row 可以不关联 ContentItem。
- provider/hash/item type/access/status allowlist、flags、JSONB shapes、internal reference checks。
- internal reference secret marker rejection，以及 raw item/source composite provenance consistency。
- provider scoped hash 与 nullable provider item ID 唯一性。
- schema allowlist 只比此前增加 `evidence_items`，不引入未来阶段表。

SQLite 不得替代 PostgreSQL 语义。测试不得访问网络或读取本地 capture。

## 7. 验收标准

- [x] migration 支持 upgrade/downgrade/re-upgrade。
- [x] `evidence_items` 字段、约束、FK、索引与批准合同一致。
- [x] PostgreSQL tests 覆盖 schema allowlist、安全 checks 与唯一性。
- [x] PostgreSQL 拒绝带 secret marker 的 raw reference 与跨 Source raw item provenance。
- [x] 只有 schema/ORM/tests/docs 变化，无 Adapter、collection、正式 normalization、dedup、Event/AI。
- [x] Foundation v2.1-FROZEN 未修改，SPEC-0022 未启动。

## 8. Delivery Evidence

以本 PR diff、PostgreSQL migration 往返、Ruff、mypy、pytest、Foundation validator 与安全 review
package 为证。PR #21 已通过 Implementation Review 并合并。

## 9. Review History

| Round | Result | Evidence | Resolution |
|---|---|---|---|
| 1 | REQUEST CHANGES | raw reference secret marker 与 Source provenance enforcement | 增加 DB secret checks、composite unique index/FK 及 PostgreSQL tests |
| 2 | PASS | PR #21，CI 与 187 tests | Implementation Review approved and merged |

PR #21 已完成：

- `evidence_items` Alembic migration；
- SQLAlchemy `EvidenceItem` ORM 与关系；
- PostgreSQL schema、constraint、upgrade/downgrade/re-upgrade tests；
- `raw_payload_reference` secret marker rejection；
- `raw_item_id` / `source_id` composite provenance consistency。

本实现未包含 Evidence Write Path、Provider Adapter、AdapterRegistry、collection/scheduler、正式
normalization、dedup/Event、AI、Telegram 或 investment recommendation。
