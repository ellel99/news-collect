# Decisions

版本：2.2-FROZEN
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

## Proposed Decisions — 等待 Foundation Revision Freeze Review

以下决定记录 2026-07-29 用户明确确认的新方向，但在新 Foundation 版本通过 Freeze Review 前不覆盖 D-001–D-019，也不授权实现。

## D-020 供应商无关的混合采集架构（Proposed）

- 建议决定：所有真实来源通过 Polling、Streaming、Webhook 或 Historical Backfill Connector 接入 Unified Ingestion Gateway；下游不得依赖具体供应商 SDK。
- 原因：允许从低成本公开来源平滑升级到商业实时数据流，避免 GDELT、NewsAPI.ai、Finnhub 或单一媒体成为不可替换核心。
- 影响：需要后续 SPEC 定义 connector contract、统一 envelope、ack/replay/backfill、许可策略和事件总线抽象；不改变已完成 SPEC-0003。
- 状态：Proposed，等待新 Foundation 版本和 Freeze Review。

## D-021 事件驱动处理与可替换内部总线（Proposed）

- 建议决定：业务链按事件驱动合同设计；首版可选 Redis Streams，未来可迁移 Kafka 或其他队列，业务模块只依赖内部消息 schema。
- 原因：同时支持轮询、流式、Webhook、断线回补和不同实时性等级。
- 影响：Redis Streams 不是当前已实现事实，也不是不可替换核心；实现前需独立 SPEC、容量/顺序/ack/重放与迁移方案。
- 状态：Proposed。

## D-022 统一逻辑新闻记录与事件主视图（Proposed）

- 建议决定：跨阶段使用统一逻辑新闻记录合同表达来源、访问许可、时间、接收序列、处理状态与后续 enrichment；最终用户主视图以 Event/EventVersion 为核心，原始 ContentItem 永久可追溯。
- 原因：统一供应商差异，并支持同源更新、跨源聚类、证据合并和一键查看重要事件。
- 影响：该合同是跨阶段逻辑聚合，不要求把所有字段放进 Phase 1 单表；Phase 1 冻结 schema 不变，新增字段/实体必须走后续 SPEC 和迁移。
- 状态：Proposed。

## D-023 三层 AI 研究与市场验证（Proposed）

- 建议决定：后续 AI 严格分离事实与证据层、投资影响分析层、研究参考层；任何研究参考必须先结合可替换市场/财务数据 adapter 验证，不得由单条新闻直接生成。
- 原因：降低把报道、观点或已定价信息误当作投资结论的风险。
- 影响：需要扩展现有 AI Rules；研究参考必须包含证据、反方观点、风险、催化剂、失效条件、适用周期与置信度，且永不自动执行。
- 状态：Proposed；涉及 D-005 的输出边界，必须 Freeze Review。

## D-024 个人优先并保留未来升级入口（Proposed）

- 建议决定：当前仍为个人自用、每天约查看 3–5 次；架构接口允许未来提高频率、接入商业数据和授权全文，并为更多用户保留升级入口。
- 原因：避免为当前规模过度设计，同时不把核心合同锁死在单机轮询。
- 影响：当前不得实现租户、Workspace、计费或多用户数据隔离；“更多用户”与 D-001 冲突，只有未来独立 Foundation/SPEC 才能启用。
- 状态：Proposed。

## Foundation Revision Impact

新版本 Freeze Review 至少必须裁决：

1. 商品、利率、外汇继续只是解释变量，还是成为直接可推荐/持有资产；
2. `HOLD`、`REDUCE_EXPOSURE`、`SMALL_POSITION_OBSERVATION` 等状态如何避免成为替用户决策的交易指令；
3. “更多用户升级入口”是否只保留接口，还是改变单用户数据与权限模型；
4. 四阶段边界是否保持，市场验证与研究参考分别进入 Phase 2、3 或新增阶段；
5. 统一逻辑新闻记录如何映射既有 Phase 1 schema，避免提前迁移。

研究状态的安全命名、动作语义限制和候选替代标签由 `AI_RULES.md` 第 11 节统一定义。

## D-025 Phase 1 technical acceptance and Event Candidate transition（Approved）

- 建议决定：基于 SPEC-0030–0038 的已审核 implementation/runtime evidence，批准四 Provider 核心
  Information Collection & Push 技术链路完成，并把下一工程阶段切换为 Event Intelligence / Event First。
- 最小新增边界：RawItem 是原始采集 trace/provenance layer；EvidenceItem 继续作为 Event Intelligence
  的事实/provenance authority；ContentItem 只是 content-safe display/projection layer，可提供安全聚类
  输入但不是事实 authority。只允许 additive EventCandidate、可审计/可撤销的 EventCandidate↔Evidence
  association、deterministic pre-dedup/rule clustering、importance contract 和 mock-only ImpactAnalyzer
  contract；EventCandidate 不删除、覆盖或替代上述既有层。
- 不授权：真实 LLM、Market Validation runtime、BUY/SELL/HOLD、投资建议、自动交易、embedding、
  vector DB、新 Provider、X 或 event-bus infrastructure。
- 追踪：SPEC-0022 的 Dedup/Event Candidate 候选范围由 SPEC-0039 absorb/supersede，避免竞争实现。
- 影响分析：见 `docs/FOUNDATION_V2_2_DRAFT.md`；v2.1-FROZEN 在 Freeze Review PASS 前继续生效。
- 状态：Approved；Foundation v2.2 Freeze Review PASS（2026-08-13）。实现严格限于 SPEC-0039。

## D-026 Target-driven unified production collection control plane（Proposed）

- 建议决定：生产调度以稳定 `CollectionTarget` 为最小 owner；每个 target 独立 cadence、typed/versioned
  config、cursor、lock、retry、run、health 和 dispatch idempotency。Source 保留 provider/授权/retention，
  SourceAccount 保留可选外部身份。
- 原因：现有四 Provider runtime 已验证，但 generic scheduler 只支持 fake，真实 scheduler 按 provider
  编排且要求单一 target，无法安全扩展多个 query/symbol/series/CIK。
- 边界：credential 仅在 worker runtime 注入；显式 factory/transport allowlist；unknown operation fail
  closed；notification/Event 与 collection 解耦；NewsAPI.ai/GDELT/X 不因本决定激活。
- Foundation conflict：v2.2 明确禁止 scheduler rewrite 并只授权 SPEC-0039；因此必须先通过
  Foundation v2.3 Freeze Review，不得在 v2.2 下直接实现。
- 状态：Proposed — SPEC-0041 Docs Review；不授权 migration 或 Python implementation，Foundation
  v2.2-FROZEN 继续生效。

## D-027 Pre-AI Collection Readiness before real model routing（Proposed）

- 建议决定：真实 AI 前先完成 unified control plane、durable safe projection、四 Provider operation
  completeness、官方来源 coverage 及 Event/Evidence/Fact completeness；之后才重新审计 deterministic+
  model routing。
- 原因：bounded smoke/default target 和 ephemeral projection 不能代表广度、恢复、许可或事实输入完整。
- PR #39：R0–R8 完成前保持 Draft；完成后基于最新 main 重新审计/rebase，不保证现有 AI contract、
  Fact digest、snapshot 或 routing 全部保留；migration 必须按串行顺序处理。
- 状态：Proposed；路线与门禁见 `docs/PRE_AI_COLLECTION_READINESS.md`，不授权代码或外部请求。
