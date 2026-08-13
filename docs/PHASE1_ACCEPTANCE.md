# Phase 1 Acceptance

版本：1.0  
适用：Phase 1 最终验收与每个 SPEC 的阶段边界检查

## 1. 主链路

```text
合法来源 → 稳定采集 → RawItem → ContentItem → 确定性去重 → 存储 → Outbox → Telegram → 运维与恢复
```

## 2. Blocker Gate

- [ ] 无 LLM、Embedding、向量数据库
- [ ] 无 Event、Evidence、Analysis
- [ ] 无 Portfolio、Holding、InvestmentPlan、CandidateRule
- [ ] 无交易建议和自动交易
- [ ] 无隐式行为收窄
- [ ] 每个真实来源有合法接入、授权、限流、Cursor、稳定 ID、Parser、保留和验证记录
- [ ] 重复抓取不产生重复 ContentItem
- [ ] 重复处理不产生重复 Telegram 消息
- [ ] Cursor 只在安全持久化后推进
- [ ] 失败可见、可分类、可重试
- [ ] Outbox 与发送幂等
- [ ] 管理 Bot 与推送 Bot 权限隔离
- [ ] 日志和 Review ZIP 无秘密
- [ ] Migration upgrade/downgrade/re-upgrade 通过
- [ ] PostgreSQL 备份和新环境恢复演练通过
- [ ] Ruff、format、mypy、pytest、Compose 和手动端到端通过
- [ ] Delivery Report、Commit Evidence 和 Review ZIP 完整

## 3. Phase 1 最终结论

- PASS：全部 Blocker 满足。
- PASS WITH ISSUES：仅有用户明确接受的非阻塞问题。
- FAIL：范围偏差、安全问题、关键可靠性失败或真实主链路不完整。

### 3.1 Core technical acceptance record（2026-08-13）

基于 SPEC-0030–0038 已合并的 implementation reviews、PostgreSQL/mock regressions 与用户批准的
bounded runtime evidence，四 Provider 核心链路 `Provider → RawItem → EvidenceItem → ContentItem →
Scheduler → Telegram` 记为 **PASS**。本记录复用既有 evidence，不重新请求 Provider 或 Telegram。

X source/account、完整 backup/restore operational exercise、management Bot 与更广 operations acceptance
仍为独立后置能力，不在本记录中虚构为完成。该 core PASS 只支持发起 Foundation v2.2 Freeze Review，
本身不授权 Event/AI implementation。

四 Provider smoke、minimal adapter/runtime 与 scheduler evidence 不等同于 multi-target unified
production control-plane acceptance。当前真实 scheduler 是已审核的 provider-level bounded path；
target-specific typed config、cadence/cursor/lock/retry/health、统一 factory 和迁移恢复仍由 SPEC-0041
Docs Review 设计。该后置项不撤销 core technical PASS，也不得把未勾选的完整 operations/
backup/restore gate 写成已完成。

v2.2 当前禁止 scheduler rewrite，因此上述后置实现还需 Foundation v2.3 Freeze Review PASS。真实 AI
必须继续等待 Pre-AI Collection Readiness R0–R8；PR #39 的 Draft 不构成 acceptance evidence 或
implementation authority。

## 4. 后续架构提案与本验收标准的关系

当前关于供应商中立混合采集、统一接入网关、可替换事件总线、Unified News Record、
事件中心化体验、三层 AI 研究链路与市场数据验证的内容，均属于下一 Foundation 版本的
候选方向。它们不改变 Phase 1 的范围、既有数据表、验收门槛或完成定义。

只有在候选方向完成影响分析、Freeze Review、Foundation 版本更新，并由后续单一 Active
SPEC 明确实现与测试要求后，相关能力才可进入工程验收。不得以本文档修订为依据，提前在
Phase 1 中增加真实来源、事件实体、AI 能力、投资组合能力或交易动作。
