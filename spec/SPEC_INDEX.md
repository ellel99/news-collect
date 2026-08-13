# SPEC Index

Foundation：v2.2-FROZEN
Current Phase：Event Intelligence foundation
Active SPEC：SPEC-0041（Docs Review only；implementation not authorized）

| SPEC | 名称 | 状态 | 依赖 |
|---|---|---|---|
| SPEC-0001 | Project Bootstrap | Completed | Foundation v2.1-FROZEN |
| SPEC-0002 | Source Registry and Phase 1 Data Model | Completed | SPEC-0001 |
| SPEC-0003 | Collection Framework, Scheduler, Cursor and Retry | Completed | SPEC-0002 |
| SPEC-0004 | First Approved Polling Source Pilot | Inactive — preflight evaluation completed；Adapter implementation not started；future implementation requires reactivation | SPEC-0003 |
| SPEC-0005 | Approved X Source and Account Collection | Planned；统一 Connector/Adapter 边界 | SPEC-0003 |
| SPEC-0006 | Raw Capture & Replay Evaluation | Completed — four-provider bounded capture/audit/replay approved | SPEC-0004 evaluation evidence |
| SPEC-0007 | Telegram Push Bot | Planned；依赖未来 Normalization/Dedup/Outbox SPEC | dependency number TBD |
| SPEC-0008 | Telegram Management Bot | Planned | SPEC-0002/0003/0007 |
| SPEC-0009 | Operations, Backup, Restore and Phase 1 Acceptance | Planned | SPEC-0001–0008 |
| SPEC-0017 | Four Provider Replay Normalization Candidate | Completed — local replay-only verification approved | SPEC-0006 |
| SPEC-0018 | Normalized Evidence Contract | Completed — Docs Review approved | SPEC-0017 |
| SPEC-0019 | Evidence Contract Implementation Scaffold | Completed — Implementation Review approved | SPEC-0018 |
| SPEC-0020 | Provider Evidence Mapping Scaffold | Completed — Implementation Review approved | SPEC-0019 |
| SPEC-0021 | Evidence Persistence / DB Schema Design | Completed — Docs Review approved | SPEC-0019/0020 |
| SPEC-0021 implementation | Evidence Persistence Schema Implementation | Completed — Implementation Review approved | SPEC-0021 Docs Review |
| SPEC-0023 | Evidence Write Path Design | Completed — Docs Review approved | SPEC-0019/0020/0021 |
| SPEC-0023 implementation | Evidence Write Path Implementation | Completed — Implementation Review approved | SPEC-0023 Docs Review |
| SPEC-0024 | Provider Adapter Integration Design | Completed — Docs Review approved | SPEC-0003/0020/0023 implementation |
| SPEC-0025 | Provider Adapter Scaffold Implementation | Completed — Implementation Review approved | SPEC-0024 Docs Review |
| SPEC-0026 | Collection Runner Adapter Registry Integration | Completed — Implementation Review approved | SPEC-0025 implementation |
| SPEC-0027 | RawItem to Evidence Pipeline Orchestration | Completed — Implementation Review approved | SPEC-0023/0026 implementation |
| SPEC-0028 | RawItem Evidence Projection Store and Pipeline Trigger | Completed — Implementation Review approved | SPEC-0027 implementation |
| SPEC-0029 | End-to-End Mock Collection Evidence Pipeline | Completed — Implementation Review approved | SPEC-0026/0027/0028 implementation |
| SPEC-0030 | Marketaux Real Adapter Implementation | Completed — Implementation Review approved | SPEC-0025/0026/0029 implementation |
| SPEC-0031 | Marketaux Bounded Live Smoke Harness | Bundled — Completed in PR #30；not separately Active | SPEC-0030 |
| SPEC-0032 | Marketaux Real Collection Pipeline | Completed — Implementation Review approved | SPEC-0026–0030 |
| SPEC-0033 | Marketaux Visible Feed + Manual Telegram Push | Completed — Implementation Review approved | SPEC-0032 |
| SPEC-0034 | Alembic Migration State Repair / Docker Startup Health | Completed — Implementation Review approved | SPEC-0033 |
| SPEC-0035 | Minimal Scheduler for Marketaux + Telegram | Completed — Implementation Review approved | SPEC-0033/0034 |
| SPEC-0036 | Multi-provider Ingestion for Finnhub, EIA and SEC EDGAR | Completed — Implementation Review approved | SPEC-0035 |
| SPEC-0037 | Multi Provider Runtime Verification | Completed — Implementation Review approved | SPEC-0036 |
| SPEC-0038 | Multi-provider Scheduler + Telegram Routing | Completed — Implementation Review approved | SPEC-0035/0037 |
| SPEC-0039 | Phase 1 Acceptance + Event Candidate Foundation | Completed — Implementation Review approved | SPEC-0018–0021/0023–0038 |
| SPEC-0041 | Unified Production Collection Control Plane | Active — Docs Review only；implementation not authorized | SPEC-0003/0030–0038；PR #39 disposition required before implementation |

仓库允许 Active SPEC 为 `None`。此时不得开始任何实现，只能进行用户明确授权的文档准备、
审查或历史收尾。`Planned` 不代表批准；`Active — Docs Review` 也只表示唯一 SPEC 文档正在
审核，不代表代码实现授权。只有用户明确批准当前 SPEC 文档且 Review PASS 后，才可另行授权
实现。不得仅因依赖已完成、候选架构已记录、SPEC 已激活或下一编号已知而开始代码工作。

## Phase 1 Planned SPEC 接入约束

### SPEC-0004 — First Approved Polling Source Pilot（historical planning record）

- SPEC-0004 的早期 preflight 文档已被后续 SPEC-0030–0038 的四 Provider implementation/runtime
  evidence 超越；不得再把“implementation not started”解释为当前四 Provider 的状态。
- NewsAPI.ai / Event Registry 当前为 future / blocked，不得 smoke。
- 历史 preflight 顺序为 Marketaux → Finnhub → EIA Open Data → SEC EDGAR；四 Provider 已在
  SPEC-0030–0038 完成当前批准的 adapter/runtime/scheduler 范围。
- 既有 PASS 不授权新 operation、任意 historical backfill 或 multi-target production capability。
- GDELT Project DOC 2.0 为 `runtime blocked / future evaluation only`；历史 evidence 保留，
  但不再是 primary pilot、不得继续 smoke 或驱动实现。
- 只能选择第一个合法、低成本或公开的 Polling Source 作为试点。
- GDELT、RSS/Atom、CNBC、Reuters、NewsAPI.ai / Event Registry、Marketaux、Finnhub 或其他单一
  provider 都不得成为核心依赖。
- 必须使用 Source Adapter / Connector / Unified Ingestion Gateway 风格的边界；这里的
  Gateway 是 provider-neutral 合同边界，不代表已经批准事件总线或新增基础设施。
- downstream 只能接收统一 envelope，不得导入 provider SDK 或解释 provider raw payload。
- SPEC 必须逐项记录 `access_level`、`license_policy`、cursor、idempotency、可用历史窗口
  和 backfill 限制；未授权能力必须 fail closed。
- 不得实现 Streaming、Webhook、Event、AI、Market Validation 或 Research Recommendation。

### SPEC-0005 — Approved X Source and Account Collection

- X source 也必须通过统一 Connector/Adapter 边界转换为统一 envelope。
- downstream 不得依赖 X provider SDK、接口专有字段或原始 payload；授权、账号范围、cursor、
  idempotency、限流、内容保留和 backfill 限制必须由该 SPEC 明确。
- 不得以 X 的实现绕过 SPEC-0004 的 provider-neutral 原则或扩大 Phase 1 范围。

### Future topic — Normalization, Deduplication and Outbox（编号待定）

- Normalization、Deduplication 与 Outbox 只能依赖统一内部合同，不得直接依赖具体 provider、
  provider SDK 或 provider raw payload。
- provider 特有映射必须留在对应 Connector/Adapter 内；不得把来源分支扩散到下游主链路。

该主题仍为 Planned，但因用户明确将编号 `SPEC-0006` 用于 Raw Capture & Replay Evaluation，
其新编号待未来文档准备时确定。`Planned` 不表示授权，不得由当前 replay scaffold 顺带实现。
SPEC-0017 只生成 normalization candidate coverage，不替代或激活该正式 pipeline 主题。
SPEC-0018 只设计 normalized evidence contract，也不授权正式 normalization pipeline。

## 候选规划（非 Active）

以下候选仅用于承接已确认的长期产品目标及待审核工程方向，不授权创建代码、迁移或新实体：

| 候选 SPEC | 目标 | 依赖 |
|---|---|---|
| SPEC-0010 Vendor-Neutral Ingestion Contracts | 定义 Polling、Streaming、Webhook、Historical Backfill 与 Unified Ingestion Gateway 合同 | SPEC-0009、Foundation revision review |
| SPEC-0011 Internal Event Bus Abstraction | 定义业务无关于 Redis Streams/Kafka 的内部消息合同、ack、replay 与迁移边界 | SPEC-0010 |
| SPEC-0012 Unified News Record and Access Policy | 定义跨阶段逻辑新闻记录、时间语义、许可与访问状态映射 | SPEC-0006、Foundation revision review |
| SPEC-0013 Event Intelligence and Evidence | 定义 Event 聚类、事实层、证据等级、版本和重要性评分 | Phase 1 acceptance、Phase 2 approval |
| SPEC-0014 Market Data Validation Adapters | 定义可替换行情/财务数据 adapter 与市场验证合同 | SPEC-0013 |
| SPEC-0015 Research Recommendation Contract | 定义研究参考状态、置信度、风险、催化剂和失效条件；不得自动执行 | SPEC-0013、SPEC-0014、AI boundary Freeze Review |
| SPEC-0016 Personal Research Home Experience | 定义 1h/6h/24h 事件视图、证据链接、等待确认和风险呈现 | SPEC-0013–0015 |
| SPEC-0022 Dedup and Event Candidate Layer | Absorbed/superseded by SPEC-0039；不得单独激活 | SPEC-0021, Foundation v2.2-FROZEN |

候选编号和顺序可在创建 Draft SPEC 时调整；不得把任何候选设为 Active，直到用户明确批准。
SPEC-0021 implementation、SPEC-0023 Docs Review/implementation、SPEC-0024 Docs Review、
SPEC-0025、SPEC-0026、SPEC-0027、SPEC-0028 与 SPEC-0029 implementation 均已 Completed。当前
SPEC-0030–0039 已完成。当前唯一 Active SPEC 为 SPEC-0041 Docs Review；Foundation
v2.2-FROZEN 生效。SPEC-0041 不授权代码或 migration。PR #39 的 SPEC-0040 是未合并 Draft，
不得由本分支合并或扩展。SPEC-0005 及其他 Planned/candidate 不代表批准；SPEC-0022 已被
SPEC-0039 absorb/supersede。
