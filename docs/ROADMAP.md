# Roadmap

版本：2.1-FROZEN  
状态：Living

## 总体路线

```text
Phase 1 信息采集与及时推送
→ Phase 2 AI 市场情报与事件整合
→ Phase 3 持仓影响与投资计划复核
→ Phase 4 显式反馈驱动的优化
```

每个阶段通过多个小型 SPEC 交付。当前 SPEC 未通过验收前，不进入下一 SPEC；当前阶段的基础可靠性未达到完成标准前，不提前开发后一阶段的复杂能力。

## Phase 1 — Information Collection & Push

### 目标

建立可靠的采集、原始留痕、确定性标准化、确定性去重、存储、Notification Outbox 和 Telegram 推送链路。

### 范围

- 项目骨架、配置、数据库、迁移和测试基础；
- Source Registry 与 Source Account；
- 新闻/RSS/API/网页适配器接口；
- X 官方或合法接口适配器；
- 原始响应留痕与标准化 Content Item；
- 稳定外部 ID 与规范化 URL 的基础去重；
- 调度、超时、退避、重试、补采和断线恢复；
- 推送队列、幂等和 Telegram 双 Bot；
- 来源健康状态、延迟指标和失败查询；
- 安全配置模板、备份与恢复说明。

### 不在本阶段

- LLM、Embedding、向量数据库；
- AI 翻译、摘要、分类、实体识别和影响分析；
- 语义事件聚合；
- 知识图谱；
- 持仓影响与投资计划提醒；
- Candidate Rule；
- 任何交易动作建议和自动交易。

### 完成标准

- 至少一个合法新闻来源和一个指定 X 来源完成端到端验证；若外部授权阻塞，使用合约测试和可替换模拟源证明框架；
- 新内容能持久化并幂等推送；
- 重复抓取不会产生重复记录或重复通知；
- 失败可见、可重试，重启后可恢复；
- `source_published_at`、`first_seen_at`、`pushed_at` 可计算；
- 管理 Bot 与推送 Bot 权限隔离；
- 自动测试、启动验证、文档和交付报告齐全。

## Phase 1 建议 SPEC 顺序

1. SPEC-0001 Project Bootstrap
2. SPEC-0002 Source Registry and Phase 1 Data Model
3. SPEC-0003 Collection Framework, Scheduler, Cursor and Retry
4. SPEC-0004 First Approved News/RSS/API Source
5. SPEC-0005 Approved X Source and Account Collection
6. SPEC-0006 Normalization, Deduplication and Outbox
7. SPEC-0007 Telegram Push Bot
8. SPEC-0008 Telegram Management Bot
9. SPEC-0009 Operations, Backup, Restore and Phase 1 Acceptance

Planned 序号不代表批准；开始前必须创建完整 SPEC 并确认。

## Phase 2 — AI Market Intelligence

### 目标

将逐条内容升级为有证据、可追溯的 Event 和市场影响解释。

### 范围

- 中文翻译、摘要、分类和实体识别；
- 事实属性与来源可信度；
- Event 识别、聚合、版本和时间线；
- 传闻、确认、修正、否认和结束状态；
- AI 全产业链与能源关系知识；
- 美股和 Crypto 的直接、间接及跨市场影响；
- 反方证据、不确定性、置信度和结论失效条件；
- Event 增量通知与判断变化通知；
- AI 输出质量反馈与评测。

### 完成标准

- 多来源同一事件能在可接受误差内合并；
- 原始 Content Item 始终保留；
- 每项关键事实可追溯到 Evidence；
- 传闻不会显示为确认事实；
- 判断变化会形成新版本并通知；
- 推送从“文章列表”升级为“事件与增量”。

## Phase 3 — Portfolio Intelligence

### 目标

结合真实持仓和用户确认的投资计划，解释事件为何与用户有关，并提醒复核风险与逻辑。

### 范围

- 美股、ETF、Crypto 和现金持仓；
- 多账户与多币种数据结构；
- 数量、成本、市值、组合占比和集中度；
- 直接、间接、产业链、宏观和跨市场暴露；
- 用户确认的 Investment Plan 与版本；
- 条件触发、逻辑支持/削弱/失效风险；
- 组合共同风险和相关性提示；
- 复核提醒、所需证据清单和 Candidate Rule。

### 明确禁止

- 自动下单；
- 自动调整计划；
- 替用户选择买入、卖出、加仓、减仓或清仓动作；
- 把条件触发当成交易指令。

### 完成标准

- 持仓映射可解释且可追溯；
- 组合集中与共同风险可计算；
- 提醒引用具体事件、证据和计划版本；
- AI 只提醒复核，不替用户决定交易；
- 用户确认前，任何候选长期规则不生效。

## Phase 4 — Investment Plan & Explicit Feedback Optimization

### 目标

根据用户明确反馈、投资计划和操作记录提高分析与提醒的适配度。

### 范围

- 用户对分析准确性、影响程度、关联关系和提醒价值的明确反馈；
- 用户主动记录的买入、加仓、减仓、卖出及原因；
- 投资计划草案、确认、版本、回滚和复盘；
- Candidate Rule 的提出、确认、拒绝和撤销；
- 分析、提示词和规则版本的审计；
- 对既有判断的决策复盘。

### 明确禁止

- 点击、打开、停留、忽略和浏览频率学习；
- 自动缩小 Collection Scope 或 Analysis Scope；
- 自动定义用户投资风格；
- 自动固化一次操作或偶然结果；
- 未确认即修改长期规则。

### 完成标准

- 每项有效规则都能追溯到用户确认；
- 用户可查看、禁用和回滚规则；
- 系统继续广泛扫描机会和风险；
- 明确反馈能改善分析，但不会造成信息茧房。

## 暂不规划

- 多用户或团队；
- Web Dashboard（可在确有需要时另行决策）；
- 其他股票市场；
- 自动执行交易；
- 大规模公开内容再分发。
