# SPEC-0021 — Evidence Persistence Schema Implementation

Status：Active — Implementation Review

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
- provider scoped hash 与 nullable provider item ID 唯一性。
- schema allowlist 只比此前增加 `evidence_items`，不引入未来阶段表。

SQLite 不得替代 PostgreSQL 语义。测试不得访问网络或读取本地 capture。

## 7. 验收标准

- [ ] migration 支持 upgrade/downgrade/re-upgrade。
- [ ] `evidence_items` 字段、约束、FK、索引与批准合同一致。
- [ ] PostgreSQL tests 覆盖 schema allowlist、安全 checks 与唯一性。
- [ ] 只有 schema/ORM/tests/docs 变化，无 Adapter、collection、正式 normalization、dedup、Event/AI。
- [ ] Foundation v2.1-FROZEN 未修改，SPEC-0022 未启动。

## 8. Delivery Evidence

以本 PR diff、PostgreSQL migration 往返、Ruff、mypy、pytest、Foundation validator 与安全 review
package 为证。Implementation Review PASS 前不得声明本实现 Completed 或开始下一 SPEC。
