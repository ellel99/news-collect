# Delivery Report

Build：SPEC-0002 Source Registry and Phase 1 Data Model
Active SPEC：SPEC-0002 — Source Registry and Phase 1 Data Model
交付日期：2026-07-29
审核状态：IN REVIEW

## 1. 本轮目标

在 PostgreSQL 16 上实现 Source Registry 与九个 Phase 1 实体的 SQLAlchemy 2.x ORM 模型、严格数据约束、索引和可逆 Alembic 迁移，为后续采集 SPEC 提供持久化契约。

## 2. Foundation 与阶段合规

- Foundation 版本：v2.1-FROZEN
- 当前阶段：Phase 1 — Information Collection & Push
- 是否修改冻结内容：否
- 是否引入后续阶段实体/依赖：否
- 是否开始 SPEC-0003：否
- 结论：实现仅限 SPEC-0002 数据模型、迁移与测试

## 3. 已完成

- 创建 `Source`、`SourceAccount`、`CollectionCursor`、`CollectionRun`
- 创建 `RawItem`、`ContentItem`
- 创建 `Notification`、`OutboxMessage`、`AuditLog`
- 使用 PostgreSQL UUID、timestamptz、JSONB 和命名 enum
- 实现 nullable、数据库默认值、检查约束、RESTRICT 外键、唯一索引和查询索引
- `raw_items.collection_run_id` 非空并引用 `collection_runs.id`
- `outbox_messages.idempotency_key` 非空且唯一
- 新增 Alembic revision `0002_create_phase1_data_model`
- 保留 SPEC-0001 的 `system_metadata`
- 增加 ORM schema allowlist/denylist、PostgreSQL 实际 schema、外键和幂等约束测试
- CI 增加 PostgreSQL 16 service 与 migration upgrade

## 4. 明确未实现

- API、CLI、管理界面
- service、repository 或 CRUD 工作流
- adapter、collector、scheduler、Celery 采集任务
- 真实来源、RSS、X、Telegram
- RawItem 获取、ContentItem 标准化或确定性去重流程
- Notification 策略或 Outbox publisher/consumer
- AI、LLM、Event、Evidence、Analysis
- Portfolio、Holding、Investment Plan、Candidate Rule
- 任何 Phase 2–4 表或交易功能

## 5. 修改文件

| 文件/目录 | 类型 | 说明 |
|---|---|---|
| `src/market_intelligence/db/models.py` | 代码 | 九个 Phase 1 ORM 模型、enum、约束、索引和关系 |
| `src/market_intelligence/db/base.py` | 代码 | 在 ORM metadata 中保留既有 `system_metadata` |
| `src/market_intelligence/db/__init__.py` | 代码 | 导出九个模型 |
| `alembic/env.py` | 迁移基础设施 | 加载模型 metadata |
| `alembic/versions/0002_create_phase1_data_model.py` | 迁移 | 九表升级、逆序回滚和 enum 清理 |
| `tests/test_models.py` | 测试 | ORM allowlist/denylist 和关键字段/索引 |
| `tests/test_postgres_models.py` | 测试 | PostgreSQL 16 schema、外键和幂等约束 |
| `.github/workflows/ci.yml` | CI | PostgreSQL 16 service 与 migration upgrade |
| `docs/CHANGELOG.md`, `spec/SPEC-0002.md` | 文档 | 实现记录与真实证据 |
| `DELIVERY_REPORT.md` | 文档 | 本报告 |

## 6. 数据库变化

### 新增表

1. `sources`
2. `source_accounts`
3. `collection_cursors`
4. `collection_runs`
5. `raw_items`
6. `content_items`
7. `notifications`
8. `outbox_messages`
9. `audit_logs`

### 保留的基础设施表

- `system_metadata`
- `alembic_version`

### 明确不存在

Event、EventVersion、EvidenceLink、Analysis、AssetImpact、PortfolioAccount、Holding、InvestmentPlan、PlanRule、CandidateRule、PlanReview 及其他未来实体。

## 7. Migration and Rollback

- Revision：`0002`
- Down revision：`0001`
- Upgrade：按 Source → SourceAccount/Run → RawItem → ContentItem → Notification 的依赖顺序创建九张表，并创建 Outbox/Audit 表和 12 个命名 enum
- Downgrade：逆序删除九张表，再删除本 revision 创建的 enum
- `system_metadata` 在 downgrade `0002 → 0001` 中保持不变
- 实测：`upgrade head → downgrade -1 → upgrade head` 全部成功
- 测试环境：本地 Docker Compose PostgreSQL 16；没有需保留的业务数据
- 非空环境风险：downgrade 会删除九张业务表及数据，执行前必须备份并明确接受数据丢失

## 8. 测试与验证结果

| 检查 | 命令 | 结果 |
|---|---|---|
| 依赖锁定 | `uv sync --frozen` | PASS；57 packages audited |
| Ruff | `uv run ruff check .` | PASS |
| 格式 | `uv run ruff format --check .` | PASS；46 files |
| mypy | `uv run mypy src` | PASS；14 source files |
| pytest | `uv run pytest` | PASS；21 tests |
| PostgreSQL 集成 | `tests/test_postgres_models.py` | PASS；4 tests，真实 PostgreSQL 16 |
| ORM/迁移一致性 | `uv run alembic check` | PASS；No new upgrade operations detected |
| Compose 静态配置 | `docker compose config` | PASS |
| PostgreSQL 启动 | `docker compose up -d postgres` | PASS；container running |
| Migration upgrade | `uv run alembic upgrade head` | PASS |
| Migration rollback | `uv run alembic downgrade -1` | PASS；`0002 → 0001` |
| Migration re-upgrade | `uv run alembic upgrade head` | PASS；`0001 → 0002` |
| Foundation | `python3 scripts/validate-foundation.py` | PASS；20 required files，Markdown links/freeze markers valid |
| Review ZIP | `bash scripts/package-review.sh` | PASS；59 files，秘密扫描无发现 |

本机全局没有 `uv` 命令。本轮复用 `/tmp/news_collect_uv/bin/uv` 0.8.3，并通过临时 `PATH` 以用户指定的 `uv ...` 命令形式执行。uv cache 和 Python 安装目录均位于 `/tmp`，未提交到仓库。

最初在默认沙箱运行数据库测试时，进程连接 `localhost:5432` 被权限策略拒绝；这是环境权限失败，不是测试断言失败。获得本地 PostgreSQL 连接权限后，迁移和全部 21 个测试均真实通过。

## 9. 数据契约重点

- 所有业务主键为数据库生成的 UUID
- 所有时间字段使用 timezone-aware timestamp
- JSONB 非空字段默认空对象
- `Source.code` 非空白且唯一；来源默认禁用
- `SourceAccount.external_id` 未知时允许 null，已知时来源范围唯一
- 所有计数非负；schedule 和 payload version 为正
- CollectionRun 完成时间不得早于开始时间
- RawItem HTTP 状态限制为 100–599
- RawItem 必须关联 CollectionRun，并支持 run/source 查询
- ContentItem 使用 `source_summary`，没有 AI 摘要字段
- ContentItem 只使用外部 ID、canonical URL 和来源范围 hash 的确定性唯一性
- Notification 和 Outbox 均具有数据库级幂等唯一键
- 外键使用 `ON DELETE RESTRICT`

## 10. 安全检查

- [x] 无真实 `.env`
- [x] 无 Token、Cookie、私钥或密码
- [x] 无本地数据库或备份进入 Git
- [x] 测试 fixture 不使用真实来源、URL、账号或正文
- [x] 测试不访问真实外部服务
- [x] Review ZIP 秘密扫描通过

## 11. 文档同步

更新 `docs/CHANGELOG.md`、`spec/SPEC-0002.md` 和本报告。未修改 Foundation、System Design、Data Model、Roadmap 或阶段边界。

## 12. 与 Active SPEC 的偏差

无。ORM metadata 与 Alembic 实际 schema 通过 `alembic check` 验证一致。

## 13. 已知问题

### Blocker

- 无已知 blocker；等待 Reviewer 审核。

### Must Fix

- 无已知 must-fix；等待 Reviewer 审核。

### Improvement

- 无。

### Future Scope

- 采集框架、调度、Cursor 推进和 retry 属于 SPEC-0003。
- 真实来源、标准化、去重、Outbox 行为和 Telegram 留待各自后续 SPEC。

## 14. Git

分支：`feat/spec-0002-source-registry-data-model`

提交信息：`feat: implement SPEC-0002 source registry data model`

## 15. 下一步

提交并创建 PR；等待 SPEC-0002 实现审核，不开始 SPEC-0003。
