# Changelog

## SPEC-0003 Implementation — 2026-07-29

### Fixed

- 使用 Redis `SET NX EX` dispatch marker，防止重复 dispatcher、Beat replay 或进程重启对同一 target/slot 重复 enqueue。
- worker 在创建 CollectionRun 前验证 account 存在、归属与 enabled，并禁止有账号来源使用 source-level target。
- account-level success 不再依据旧历史 run 推断“本轮全部成功”，避免掩盖其他账号失败。

### Added

- 来源无关的 collection contract、仅含 fake adapter 的 registry，以及确定性 dispatcher。
- Celery collection tasks、Redis owner-token lease、CollectionRun 生命周期、RawItem/cursor 原子 checkpoint、retry/error classification、source health 和 stale run recovery。
- 单元、PostgreSQL 16、Redis/Celery 与故障注入测试。

### Changed

- 增加无秘密 collection 配置模板和 Celery Beat schedule。
- CI 增加 Redis 7 service。

### Scope

- 无 schema 变化、无 Alembic revision、未修改 ORM 模型或 Foundation。
- 未接入真实来源、Normalization/Dedup/Outbox、Telegram、AI 或未来阶段实体。

## SPEC-0003 Collection Framework, Scheduler, Cursor and Retry — 2026-07-29

### Added

- 创建 `spec/SPEC-0003.md`，严格定义下一阶段的 adapter contract、调度框架、CollectionRun 生命周期、cursor checkpoint、retry、错误分类、测试和验收要求。

### Changed

- 将 SPEC-0002 标记为 Completed；
- 将 SPEC-0003 设为唯一 Active SPEC，并更新 AI Context 入口。

### Scope

- 本次仅创建和激活规格文档，未开始 SPEC-0003 实现；
- 未新增代码、迁移、数据库表、真实来源、Telegram、Normalization/Dedup/Outbox 行为、AI 或后续阶段实体；
- 未修改 Foundation v2.1-FROZEN；实现须在 SPEC-0003 文档经用户或 Reviewer 审核 PASS 后开始。

## SPEC-0002 Implementation — 2026-07-29

### Added

- 九个 Phase 1 SQLAlchemy ORM 模型：Source、SourceAccount、CollectionCursor、CollectionRun、RawItem、ContentItem、Notification、OutboxMessage、AuditLog；
- PostgreSQL 16 原生 UUID、timestamptz、JSONB、enum、检查约束、外键、唯一索引和查询索引；
- Alembic revision `0002_create_phase1_data_model`，支持 `upgrade → downgrade -1 → upgrade`；
- PostgreSQL 集成测试、ORM/迁移一致性检查以及 schema allowlist/denylist 测试；
- CI PostgreSQL 16 service 和 migration upgrade 步骤。

### Changed

- Alembic metadata 现在加载九个 Phase 1 模型，并保留 SPEC-0001 的 `system_metadata` 基础设施表；
- `raw_items` 通过非空 `collection_run_id` 追溯采集运行；
- `outbox_messages` 使用非空且唯一的 `idempotency_key` 防止 retry 重复发布记录。

### Scope

- 未实现 API、CLI、service、repository、adapter、collector、scheduler、真实来源、Telegram、标准化、Notification 策略或 Outbox 发布；
- 未创建 Event、Evidence、Analysis、Portfolio、Holding、Investment Plan、Candidate Rule 或其他未来阶段实体；
- 未修改 Foundation v2.1-FROZEN。

## SPEC-0002 Source Registry and Phase 1 Data Model — 2026-07-29

### Added

- 创建 `spec/SPEC-0002.md`，严格定义下一阶段的实现范围、非范围、九个 Phase 1 数据实体、字段、约束、迁移、测试与验收要求。

### Changed

- 将 SPEC-0001 标记为 Completed；
- 将 SPEC-0002 设为唯一 Active SPEC，并更新 AI Context 入口。

### Scope

- 本次仅创建和激活规格文档，未开始 SPEC-0002 实现；
- 未新增模型、迁移、业务代码、真实来源、Telegram、AI 或后续阶段实体；
- 未修改 Foundation v2.1-FROZEN；实现须在 SPEC-0002 文档经用户或 Reviewer 审核 PASS 后开始。

## SPEC-0001 Project Bootstrap — 2026-07-29

### Fixed

- `.env.example` 改为宿主机 `localhost` 配置，Compose 暴露 PostgreSQL/Redis 端口并保留容器内部服务名；
- `session_scope` 改为异步 context manager，并增加生命周期测试；
- `DATABASE_URL` 仅允许 SPEC 固定的 PostgreSQL asyncpg URL；
- 完成 Compose 健康、Alembic 往返与 Celery health task 的真实运行时验收。

### Added

- Python 3.12 `src/` 项目骨架、FastAPI 应用与 live/ready 健康接口；
- Pydantic Settings、结构化日志和关联 ID；
- SQLAlchemy 2.x、Alembic 及仅含 `system_metadata` 的首条基础迁移；
- Redis 7、Celery 5 worker、Celery Beat 与无副作用健康任务；
- uv 锁文件、Dockerfile、Docker Compose、pytest、Ruff、mypy 与 GitHub Actions CI；
- 安全配置模板、启动/迁移/测试/排错文档和交付报告。

### Scope

- 未修改 Foundation v2.1-FROZEN；
- 未引入任何 Phase 1 业务实体、真实来源、Telegram、AI 或后续阶段实体。

## Foundation v2.1-FROZEN — 2026-07-28

### Frozen

- 单用户、美股/ETF/Crypto、Broad Scan、Controlled Push、四阶段边界、自动交易禁止和 Phase 1 技术基线。

### Changed

- Phase 1 收敛为采集、原始留痕、确定性标准化、确定性去重、存储、Outbox 和 Telegram 推送；
- Phase 1 改为 Content First，Phase 2 起 Event First；
- AI/Event/Evidence 移至 Phase 2；Portfolio/Investment Plan 移至 Phase 3；
- P0–P4 改为 Phase 1 可执行的确定性规则；
- 管理 Bot 收缩为运维管理；
- Source Catalog 增加实现契约；
- uv 固定为依赖与锁文件工具。

### Added

- FOUNDATION_FROZEN.md；
- docs/PHASE1_ACCEPTANCE.md；
- docs/FREEZE_REVIEW.md；
- spec/SPEC_INDEX.md；
- scripts/validate-foundation.py；
- MANIFEST.sha256。

## Foundation v2.0 — 2026-07-28

### Added

- 单用户、美股与 Crypto 的正式项目边界；
- Collection、Analysis、Notification、Portfolio 四个 Scope；
- Broad Scan 与 Controlled Push；
- P0–P4 通知定义；
- Content Item、Event、Evidence、Investment Plan 等统一术语；
- Source Catalog 与稳定账号 ID 验证要求；
- 核心决策记录；
- 可审计的 SPEC 和 Delivery Report 模板；
- Phase 1 可执行技术基线；
- 安全打包与秘密扫描脚本。

### Changed

- Phase 3 改为持仓影响、组合风险和投资计划复核；
- 删除 AI 替用户选择买卖、加减仓等交易动作的权限；
- 长期优化改为显式反馈和用户确认；
- Telegram 被定义为主要入口而非整个服务端后台；
- 原始 Content Item 与 Event 分层保存；
- 项目信息源只保存当前有效知识，Git 保存历史。

### Removed

- 多用户、Workspace、团队和 SaaS；
- 隐式点击、打开、忽略行为学习；
- 自动缩小信息覆盖；
- 自动修改投资计划；
- 自动交易和确定性投资指令；
- 以未授权方式获取或保存付费正文。
