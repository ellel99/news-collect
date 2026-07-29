# Glossary

版本：2.1-FROZEN

| 术语 | 定义 |
|---|---|
| Source | 逻辑信息来源，如 Reuters、X、SEC。 |
| Source Account | Source 下的账号、栏目、Feed 或端点。 |
| Adapter | 针对某接入方式的采集实现。 |
| Connector | 按 Polling、Streaming、Webhook 或 Historical Backfill 模式接入 provider，并转换为统一 ingestion contract 的边界组件。 |
| Provider | 提供新闻、公告、市场或财务数据的供应商/承载方；不等同于原始发布者 Source。 |
| Unified Ingestion Gateway | 验证、接收并发布统一 ingestion envelope 的供应商无关边界。 |
| Internal Event Bus | 进程间传递内部消息的抽象合同；Redis Streams/Kafka 是可替换实现，不是业务模型。 |
| Polling Source | 通过定时查询 REST、RSS、网页或公告获取增量的来源模式。 |
| Streaming Source | 通过长连接、WebSocket、Kafka 或商业实时流连续接收消息的来源模式。 |
| Webhook Source | 由 provider 主动推送并要求签名、幂等和 replay protection 的来源模式。 |
| Historical Backfill Source | 按时间窗或历史 cursor 回补缺失数据的来源模式。 |
| Unified News Record | 跨 RawItem、ContentItem 和后续 enrichment 的逻辑投影视图，不等同于单一数据库表。 |
| Collection Run | 一次可审计的采集执行。 |
| Raw Item | 原始响应或其最小合法留痕。 |
| Content Item | 标准化后的单条文章、帖子、公告或 Feed 项。项目中不再用 `News` 作为通用模型名。 |
| Content First | Phase 1 以 RawItem 和 ContentItem 为核心，不创建 Event。 |
| Event First | Phase 2 起以 Event/EventVersion 为主要呈现对象，同时保留 ContentItem。 |
| Event | 一个现实世界事件，可由多个 ContentItem 支持并持续更新。 |
| Event Membership | Content Item 与 Event 的关联及角色。 |
| Event Version | Event 在某一时点的事实、状态和判断快照。 |
| Evidence | 支持、修正、否认或补充某项事实的可追溯内容。 |
| Analysis | 对 EventVersion 的结构化 AI 输出。 |
| Asset | 可持有资产：美股、ETF、Crypto 或现金。 |
| Entity | 公司、人物、国家、产品、政策、行业等非持仓对象。 |
| Asset Impact | Event 对 Asset 的方向、路径、周期和置信度。 |
| Holding | 某账户中某 Asset 的实际持仓。 |
| Investment Plan | 用户确认的资产投资逻辑、关注因素、风险因素和复核条件版本。 |
| Plan Rule | Investment Plan 中已确认的观察、风险、复核或失效条件。 |
| Candidate Rule | AI 建议但尚未获得用户确认的候选条件。 |
| Plan Review | Event 对有效 Investment Plan 的支持、削弱或复核判断。 |
| Operation Record | 用户明确确认的真实买入、加仓、减仓、卖出或其他操作记录。 |
| Notification | 向 Telegram 等渠道投递的消息记录。 |
| Alert | 用户可见的提醒语义；落库统一使用 Notification，不建立重复 Alert 通用模型。 |
| Priority | P0–P4 投递紧急等级；Phase 1 不等同于 AI 影响、持仓风险或价格方向。 |
| Priority Reason | 产生 Priority 的确定性理由。 |
| Policy Rule ID | 产生 Notification 决策的规则标识。 |
| Severity | 影响程度 1–5。 |
| Confidence | 对某事实、匹配或分析判断的确信程度。 |
| Access Level | 内容访问状态：公开全文、公开摘要、需订阅、已授权、仅链接或阻塞。 |
| License Policy | 对获取、保存、处理和展示内容的许可约束。 |
| Research Recommendation | 经事实、影响和市场数据验证后形成的可追溯研究参考；不是收益保证或自动交易指令。当前仍属 Proposed。 |
| Broad Scan | 不因隐式行为缩小 Collection Scope 和 Analysis Scope。 |
| Controlled Push | 根据明确规则控制 Notification Scope。 |
| Collection Scope | 系统获取的信息范围。 |
| Analysis Scope | 系统标准化、事件化和分析的信息范围。 |
| Notification Scope | 系统主动发送的信息范围。 |
| Portfolio Scope | 映射到持仓和计划的信息范围。 |
| Active SPEC | 当前唯一允许实施的功能规格。 |
| Delivery Report | 每次交付对范围、变更、验证、风险和偏差的结构化报告。 |
| Review ZIP | 去除秘密、缓存、运行数据和构建产物后用于审核的完整项目包。 |

## 避免使用的歧义词

- `News`：仅用于自然语言，不作为统一实体名；
- `Signal`：除非后续 SPEC 明确定义，否则不建立 Signal 实体；
- `Recommendation`：投资场景统一使用“影响分析”或“复核提醒”，避免暗示交易指令；
- `Learning`：只指显式反馈确认后的优化，不包含隐式点击学习；
- `Backend`：指服务端管理能力，不等同于 Web Dashboard；
- `Summary`：Phase 1 必须区分来源摘要与未来 AI 摘要。
