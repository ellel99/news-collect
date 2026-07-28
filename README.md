# Market Intelligence Collector

单用户、自用的美股与 Crypto 实时市场情报系统。

系统按四个阶段演进：

1. 稳定采集信息并及时推送；
2. 使用 AI 做事件整合、事实提取和跨市场影响分析；
3. 结合真实持仓与用户确认的投资计划做风险映射和复核提醒；
4. 根据用户明确反馈持续优化，但不根据点击、忽略或浏览行为缩小信息范围。

## 冻结状态

- Foundation：v2.1-FROZEN
- 状态：Frozen
- 当前阶段：Phase 1 — Information Collection & Push
- 开发入口：`spec/SPEC-0001.md`

Phase 1 固定主链路：

```text
Source Registry
→ Collection
→ Raw Item
→ Deterministic Normalization
→ Content Item
→ Deterministic Deduplication
→ Storage
→ Notification Outbox
→ Telegram Push
→ Operations / Health / Audit
```

Phase 1 不包含 LLM、AI 摘要、Event、Evidence、Portfolio、Holding、Investment Plan、Candidate Rule 或交易建议。

## 核心边界

- 覆盖市场：美股、ETF、Crypto，以及解释二者所需的宏观、政策、AI 产业链和能源信息。
- 第一阶段交互入口：Telegram 管理 Bot 与情报推送 Bot。
- 系统不自动交易，不自动下单，也不输出替用户作出买卖决定的指令。
- AI 不得擅自修改投资计划、风险规则或持仓逻辑。
- 付费和受版权保护内容只在合法授权范围内接入和保存。

## 文档阅读顺序

1. [`AI_CONTEXT.md`](AI_CONTEXT.md)
2. [`docs/FOUNDATION.md`](docs/FOUNDATION.md)
3. [`docs/ROADMAP.md`](docs/ROADMAP.md)
4. [`docs/SYSTEM_DESIGN.md`](docs/SYSTEM_DESIGN.md)
5. [`docs/DATA_MODEL.md`](docs/DATA_MODEL.md)
6. [`docs/AI_RULES.md`](docs/AI_RULES.md)
7. 当前 [`spec/`](spec/) 中的 Active SPEC

来源、术语、历史决策和开发流程分别记录在：

- [`docs/SOURCE_CATALOG.md`](docs/SOURCE_CATALOG.md)
- [`docs/GLOSSARY.md`](docs/GLOSSARY.md)
- [`docs/DECISIONS.md`](docs/DECISIONS.md)
- [`docs/DEVELOPMENT_WORKFLOW.md`](docs/DEVELOPMENT_WORKFLOW.md)

## 当前状态

- Foundation：v2.1-FROZEN
- 当前阶段：Phase 1 — Information Collection & Push
- Active SPEC：[`spec/SPEC-0001.md`](spec/SPEC-0001.md)
- 业务代码：尚未开始

## 目录

```text
.
├── README.md
├── AI_CONTEXT.md
├── docs/
├── spec/
└── scripts/
```

该仓库中的 Markdown 文档是项目设计基线；代码、迁移、测试和交付报告描述当前实现事实。冻结规则见 `FOUNDATION_FROZEN.md`，Phase 1 最终验收见 `docs/PHASE1_ACCEPTANCE.md`。
