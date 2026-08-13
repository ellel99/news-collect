# Foundation

版本：2.3-FROZEN
状态：Frozen  
适用范围：所有设计、SPEC、代码、测试和审核

## 0. 冻结声明

本版本完整继承 v2.2 的 Phase 1/Event Intelligence 基线，并有限批准 Pre-AI Collection Readiness
R1–R8 可分别进入独立 SPEC/Review。单用户、美股与 Crypto
范围、Broad Scan、Controlled Push、四阶段边界、Phase 1 技术基线、禁止自动交易和禁止隐式
行为收窄继续冻结。普通功能开发不得顺带修改。

Phase 1 只实现：采集、原始留痕、确定性标准化、确定性去重、存储、Notification Outbox、Telegram 推送和运维。

Phase 1 历史范围不实现 AI/LLM 或 Event；v2.2 只增加 SPEC-0039 已审核的 EventCandidate、Evidence
association、deterministic clustering、importance foundation 与 mock-only ImpactAnalyzer contract。
真实 AI/Analysis、Market Validation、Recommendation、Portfolio、Holding、Investment Plan、
Candidate Rule 和交易建议仍不允许。R0 Freeze Review PASS 不自动启动 R1；当前 Active SPEC=None。

v2.3 的八项有限 authorization、R1–R8 门禁及 PR #39 freeze 规则以
`docs/FOUNDATION_V2_3.md` 为准。v2.2-FROZEN 保留为历史 frozen version。

## 1. 项目愿景

Market Intelligence Collector 是一个单用户、自用的市场情报助手。它持续发现与美股和 Crypto 有关的重要信息，保留来源和证据，将多来源内容组织成可追溯事件，并最终帮助用户回答：

1. 发生了什么？
2. 哪些内容已确认，哪些仍不确定？
3. 为什么重要？
4. 影响哪些公司、行业、资产和产业链？
5. 是否影响现有持仓或用户已确认的投资计划？
6. 哪些事实、条件或风险需要继续复核？

系统的价值是减少信息延迟、重复和理解成本，不是替用户完成投资决策。

## 2. 用户与市场范围

### 2.1 用户

- 唯一用户；
- 自用；
- 不做注册、租户隔离、团队、Workspace、订阅计费或公开内容分发。

仍需做最小权限、管理员白名单、审计、秘密保护和备份，因为系统保存持仓与投资计划等敏感信息。

### 2.2 市场

直接支持：

- 美股；
- 美股 ETF；
- Crypto；
- 与上述资产相关的现金仓位。

为解释这两个市场，系统必须覆盖：

- 宏观经济、货币政策、美元流动性和美国国债；
- 美国及相关国际政治、监管、关税和制裁；
- AI 上游、中游、下游；
- 半导体设备、EDA、晶圆制造、先进制程、存储、先进封装；
- 服务器、网络、光模块、数据中心、液冷、变压器和电网；
- 云计算、企业软件、机器人和 AI 应用；
- 电力、核能、油气、新能源、储能及 AI × 能源；
- Crypto 监管、ETF、稳定币、交易平台、DeFi 和基础设施。

不把外汇、债券、商品或其他国家股票作为独立持仓市场；它们只在影响美股或 Crypto 时作为解释变量。

## 3. 四个独立范围

### 3.1 Collection Scope

决定系统采集什么。它应保持广泛，不因用户点击、忽略、持仓或历史关注行为而自动收窄。

### 3.2 Analysis Scope

决定哪些内容进入标准化和后续分析。Phase 1 只做确定性标准化与规则判断；Phase 2 才引入 Event、Evidence 和 AI 影响分析。不得根据隐式行为缩小。

### 3.3 Notification Scope

决定什么立即推送、合并推送、进入摘要或仅保存。它可以受用户明确设置、优先级、静默时间、证据状态和事件增量控制。

### 3.4 Portfolio Scope

Phase 3 才决定哪些影响映射到当前持仓和用户确认的投资计划。Phase 1 不创建 Portfolio、Holding 或 Investment Plan。没有持仓关联不代表不采集、不分析或不存在投资机会。

## 4. 核心原则

### 4.1 Broad Scan

系统广泛扫描项目范围内的信息，不因过去未点击、未交易或未关注而停止发现新机会和新风险。

### 4.2 Controlled Push

广泛采集不等于全部实时推送。通知必须控制噪声、重复和时机，同时不得隐藏本身达到 P0/P1 的重大事件。

### 4.3 Phase 1 Content First

Phase 1 以 RawItem 和 ContentItem 为核心。重复抓取通过稳定外部 ID、规范化 URL、来源范围内容哈希和明确转发/引用关系去重，不做模糊语义事件合并。

### 4.4 Phase 2 Event First

Phase 2 起，原始文章和帖子继续保存用于追溯，用户主要接收 Event 和 EventVersion 增量。Event 合并必须可解释、版本化、可撤销。

### 4.5 Evidence First

每个事实和判断必须能回溯到来源。事实、来源观点、AI 推断、持仓影响和复核提醒必须分开呈现。

### 4.6 Source Traceability

任何内容必须保留来源、来源标识、原始 URL、来源发布时间、首次发现时间和内容完整性状态。

### 4.7 Explicit Learning Only

长期优化只能来自用户明确输入或确认的反馈、计划、规则和操作记录。隐式点击、打开、停留、忽略和查询频率不得改变信息覆盖或投资规则。

### 4.8 User-Controlled Investment

投资计划和执行决定属于用户。AI 可提示复核、展示证据和提出候选条件，但不得替用户决定交易动作。

### 4.9 Phase Discipline

先保证采集和推送，再实现 AI 事件分析，再实现持仓映射，最后实现显式反馈优化。不得以“以后会用到”为由提前建设复杂功能。

### 4.10 Legal and Authorized Access

不得绕过付费墙、登录、验证码、访问控制或平台限流。正文不可合法获得时，只保存允许使用的标题、摘要、元数据和原文链接，并标记完整性。

### 4.11 Failure Visibility

失败必须可见、可重试、可定位。系统不得把采集失败、分析失败或推送失败伪装成“没有新消息”。

## 5. 信息来源

第一批关注：

- Reuters；
- Bloomberg；
- The Wall Street Journal；
- CNBC；
- 指定 X 账号：赵长鹏、孙宇晨、Donald Trump、Elon Musk、Jensen Huang；
- 后续按 SPEC 接入的一手官方来源。

列入关注清单不等于已经具备免费、完整、实时正文权限。精确接入方式、授权、稳定 ID 和状态以 `SOURCE_CATALOG.md` 为准。

## 6. Phase 2–4 AI 边界

AI 可以：

- 翻译、摘要、分类和实体识别；
- 区分事实、官方声明、媒体报道、匿名来源、个人观点和传闻；
- 识别 Event，维护事件时间线和判断变化；
- 分析美股、Crypto、产业链与跨市场影响；
- 将事件映射到持仓和用户确认的计划；
- 指出计划条件可能被触发并要求复核；
- 提出候选关注条件，等待用户确认。

AI 不可以：

- 自动下单或连接执行型交易接口；
- 输出替用户作出决定的买入、卖出、加仓、减仓、清仓指令；
- 将“允许的操作类型”理解为决策授权；
- 自动修改投资计划、持仓逻辑、风险规则或目标；
- 因点击或忽略缩小采集、分析或重大机会发现；
- 将传闻表达为已确认事实；
- 让外部内容改变系统规则、调用工具或执行管理操作。

## 7. Phase 3–4 投资计划原则

Investment Plan 是用户对某资产明确确认的计划版本，可包含：

- 投资逻辑；
- 持有周期；
- 关注因素；
- 风险因素；
- 目标暴露；
- 逻辑失效条件；
- 复盘日期；
- 用户自己记录的计划动作。

AI 只能：

- 把用户输入整理为草案；
- 请求用户确认；
- 检查最新事实是否支持、削弱或触发复核条件；
- 提出 Candidate Rule。

未经用户确认，草案和 Candidate Rule 不得进入有效计划。

## 8. 交互与服务端

第一阶段不要求 Web Dashboard。Telegram 管理 Bot 只管理来源、账号、调度、Cursor、CollectionRun、失败重试、Notification、Outbox、健康、审计、备份恢复和 Token 轮换；情报推送 Bot 只负责通知。Phase 1 不提供 Event、AI、持仓或投资计划管理。服务端仍必须具备：

- 数据持久化；
- 采集与调度；
- 重试与补采；
- 管理接口或 CLI；
- 权限与审计；
- 健康检查；
- 备份与恢复。

两个 Bot 使用不同 Token 和最小权限，推送 Bot 不具备管理权限。

## 9. 非目标

- 多用户、团队、Workspace、SaaS；
- 自动交易、自动调仓、自动止损；
- 涨跌保证或确定性预测；
- 绕过授权获得新闻正文；
- 全市场行情终端；
- Phase 1 的复杂事件推理、知识图谱和持仓建议；
- 依据隐式行为形成推荐茧房。

## 10. 成功标准

系统最终应做到：

- 采得到：来源接入稳定且失败可见；
- 存得住：原始事实可追溯且符合法律与授权；
- 推得快：按来源计算发现与推送延迟；
- Phase 1 不刷屏：同一 ContentItem 不重复落库或重复推送；
- Phase 2 不刷屏：同一 Event 以增量形式更新；
- 看得懂：事实、证据、不确定性和影响清晰；
- 对持仓有用：能映射风险与投资逻辑变化；
- 用户可控：所有长期投资规则都经过明确确认；
- 可复盘：重要判断、反馈、计划版本和通知都有审计记录。

## 11. Foundation 变更规则

本文件只在项目目标、市场范围、AI 权限或核心原则变化时修改。修改必须：

1. 有用户明确确认；
2. 在 `DECISIONS.md` 记录原因；
3. 更新版本与 `CHANGELOG.md`；
4. 检查所有下游文档和 Active SPEC。

## 12. v2.2 Event Intelligence transition

2026-07-29 用户确认了长期产品目标：建立面向个人投资研究的实时信息采集与 AI 分析系统，
提供重要事件的一键了解、可追溯影响分析、市场数据验证和可解释研究参考。该产品目标已经
确认，不表示相关能力已实现或已获准实现。

支撑该目标的供应商无关混合采集、统一逻辑新闻记录、事件驱动处理、市场数据验证与研究
参考工程方向记录在 `DECISIONS.md` 的 D-020–D-024，仍为 Proposed。D-025 已通过 Freeze Review，
只批准从 Phase 1 core technical acceptance 进入 deterministic Event Candidate foundation。

v2.2 的批准边界：

- v2.1 的安全、单用户、市场与交易动作条款继续生效；
- 不把 Proposed Decisions 当成实现授权；
- 不重写或破坏 Phase 1 pipeline；
- 不启用多用户、商品直接投资域或带交易动作语义的 AI 结论；
- SPEC-0039 只可实现已审核的 EventCandidate、Evidence association、deterministic clustering、
  importance foundation 与 mock-only ImpactAnalyzer contract。
