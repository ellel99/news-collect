# Decisions

版本：2.1-FROZEN  
状态：Frozen core decisions; append-only

## D-001 单用户、自用

- 决定：不建设多用户、租户、Workspace、团队和计费。
- 原因：项目服务唯一用户，避免无价值复杂度。
- 后果：仍保留最小权限、审计和敏感数据保护。

## D-002 只覆盖美股与 Crypto

- 决定：持仓和跨市场核心仅为美股、ETF、Crypto。
- 原因：符合用户实际需求。
- 后果：利率、美元、债券、能源等作为影响变量，不扩展为独立交易市场。

## D-003 Broad Scan 与 Controlled Push 分离

- 决定：采集/分析覆盖不受隐式行为收窄，通知单独控制。
- 原因：避免遗漏新投资机会和重大风险。
- 后果：必须分别建模 Collection、Analysis、Notification、Portfolio Scope。

## D-004 禁止隐式行为学习

- 决定：不使用点击、打开、停留、忽略和查询频率自动改变覆盖或投资规则。
- 原因：会形成信息茧房并可能造成机会流失。
- 后果：长期优化只接受用户明确输入或确认。

## D-005 用户控制投资计划

- 决定：AI 不得自动修改 Investment Plan 或选择交易动作。
- 原因：投资判断和执行权属于用户。
- 后果：AI 只做影响分析、条件检查、复核提醒和 Candidate Rule。

## D-006 Phase 1 Content First，Phase 2 Event First

- 决定：Phase 1 以 RawItem/ContentItem 为核心；Phase 2 以后以 Event 为主要呈现对象，同时永久保留 ContentItem。
- 原因：先验证数据质量和可靠推送，避免 Phase 1 被语义合并扩大。
- 后果：Phase 1 不创建 Event 表；Event 合并必须可解释、版本化、可撤销。

## D-007 两个 Telegram Bot

- 决定：管理 Bot 与情报推送 Bot 分离。
- 原因：避免管理消息与实时消息混杂，并降低 Token 泄露后的权限影响。
- 后果：不同 Token、不同进程或权限集，推送 Bot 无管理权限。

## D-008 暂不建设 Web Dashboard

- 决定：Phase 1 使用管理 Bot、CLI 或受保护 API。
- 原因：单用户项目优先验证核心链路。
- 后果：服务端管理能力仍必须完整；未来有明确痛点再决策 Dashboard。

## D-009 分阶段实现

- 决定：采集推送 → AI 情报 → 持仓复核 → 显式反馈优化。
- 原因：先验证稳定性，避免复杂 AI 掩盖数据质量问题。
- 后果：Active SPEC 不得跨阶段提前实现。

## D-010 合法授权优先

- 决定：不绕过付费墙、登录、验证码、限流或平台限制。
- 原因：合规、稳定性和版权边界。
- 后果：无法合法获得正文时保存允许的元数据、摘要和链接；来源可标记 blocked。

## D-011 术语统一为 Content Item

- 决定：统一标准内容实体名为 `ContentItem`。
- 原因：同时覆盖文章、X Post、公告和 Feed，避免 News/Article/Post 混用。
- 后果：具体类型通过 `content_kind` 表示。

## D-012 Phase 1 技术基线

- 决定：Python 3.12、uv、FastAPI、Pydantic Settings、SQLAlchemy 2.x、Alembic、PostgreSQL 16、Redis 7、Celery 5、Celery Beat、httpx、Docker Compose、pytest、Ruff、mypy。
- 原因：成熟、可测试、适合采集任务和后续 AI 服务。
- 后果：替换核心组件必须通过新决策和 SPEC。

## D-013 项目信息源不是版本控制

- 决定：Git 保存历史；项目信息源只保存当前有效知识；ZIP 用于交付和审核。
- 原因：避免多个“最新版”冲突。
- 后果：文件名保持稳定，历史通过 Git 和 Release 保存。

## D-014 一个 Active SPEC

- 决定：当前功能未 PASS 前不开始下一功能。
- 原因：保证代码、测试、文档和审核闭环。
- 后果：每个功能可经历多轮修复，但不得跨功能扩张。

## D-015 Phase 1 范围冻结

- 决定：Phase 1 只做采集、确定性标准化、确定性去重、存储、Notification Outbox、Telegram 推送和运维。
- 原因：用户明确要求第一阶段是单纯的信息采集与推送。
- 后果：AI、Event、Evidence、Portfolio、Investment Plan、Candidate Rule 和交易建议全部不属于 Phase 1。

## D-016 Phase 1 Priority 必须确定性

- 决定：P0–P4 只来自来源、账号、内容类型、关键词、来源标记、时间和用户显式配置。
- 原因：Phase 1 没有 AI、Event 和持仓。
- 后果：Notification 保存 priority_reason、policy_rule_id、policy_version。

## D-017 Transactional Outbox

- 决定：Telegram 可靠投递采用 Transactional Outbox 或经验证的等价设计。
- 原因：数据库提交和外部发送不能依赖单一同步调用。
- 后果：发送幂等、失败重试、provider_message_id 和状态必须持久化。

## D-018 Source Catalog 是接入契约

- 决定：来源名称不等于接入批准。
- 原因：避免编码工具猜测抓取方式。
- 后果：真实 Adapter 必须有独立 SPEC 和合法接入证据；SPEC-0001 不接真实来源。

## D-019 Foundation v2.1 冻结

- 决定：v2.1-FROZEN 作为 Phase 1 开发基线。
- 原因：业务边界、技术基线和第一阶段范围已具备开发条件。
- 后果：修改冻结内容需要用户明确确认、新 Decision、新版本和 Freeze Review。
