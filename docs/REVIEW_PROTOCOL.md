# Architecture and Pull Request Review Protocol

状态：ACTIVE

适用范围：本仓库的架构、SPEC、Docs Review、Implementation Review、migration、Provider、runtime、数据完整性与 AI readiness 审核。

## 1. 目的

本协议防止审核在发现第一个问题后过早停止，导致同一功能被多轮、零散、反复修改。

审核者必须先完成约定范围的完整检查，再一次性输出问题清单。普通设计错误、字段遗漏或合同矛盾只能被记录，不能成为提前结束整轮审核的理由。

## 2. 唯一允许提前停止的情况

只有以下情况可以在覆盖检查完成前停止：

- 继续操作可能修改、泄露或破坏数据；
- credential、安全边界或未经授权的真实外部请求存在风险；
- 权限、缺失提交或缺失文件使后续内容客观上无法读取；
- 审核基线 SHA、merge base 或 PR 内容无法可靠确定。

普通架构缺陷、测试遗漏、migration 矛盾和文档不一致不属于提前停止条件。

## 3. 强制三遍审核

### Pass 1：完整覆盖

在输出修改建议前必须检查：

- 审核 commit SHA、目标分支和 merge base；
- 相对目标分支的完整 diff，而不只是提交摘要；
- 所有修改文件及其直接引用的合同；
- 与变更相关的当前实现、ORM、Alembic、runtime、config 和测试；
- SPEC、README、AI_CONTEXT、DATA_MODEL、SYSTEM_DESIGN、ROADMAP、DECISIONS、CHANGELOG 等状态入口；
- 工作区状态及与审核无关的用户修改。

本遍只收集问题，不生成 Codex 修改命令。

### Pass 2：关联追踪

每个发现必须沿以下链路检查到底：

```text
字段/合同
→ 数据来源与 ownership
→ 数据库约束
→ service/runtime 行为
→ lifecycle 与 immutability
→ concurrency/idempotency
→ migration/backfill/upgrade
→ downgrade/rollback/recovery
→ tests
→ 跨文档一致性
```

例如发现 cursor identity 问题时，必须同时检查 NULL、初始化、唯一性、暂停/恢复、ownership transfer、并发、Migration B、rollback 和测试，不能在发现第一个约束缺口后结束。

### Pass 3：反向验证

从目标能力反推合同和实现：

- 真实代码能否实现，而不是只在文字上成立；
- restart、retry、partial failure 和并发下是否仍成立；
- rollback/downgrade 后是否仍保持 provenance 和幂等；
- 安全、许可、credential 和内容边界是否被保持；
- 数据是否在 Provider→RawItem→Content/Evidence→Event/Fact 链路被写死、裁剪或丢失；
- 测试能否证明行为，而不只是覆盖正常路径；
- 其他规范是否仍保留被最终合同推翻的旧设计。

三遍均完成前，审核状态只能是 `INCOMPLETE REVIEW`。

## 4. 强制审核矩阵

每轮审核至少确认以下项目：

- [ ] 完整 diff 与实际 main 基线
- [ ] schema、ORM、字段来源和数据 ownership
- [ ] migration upgrade、backfill、deployment ordering
- [ ] downgrade、rollback、forward recovery
- [ ] lifecycle、immutability、状态转换
- [ ] concurrency、locking、idempotency、stale work
- [ ] scheduler、worker、retry、runtime 和 restart
- [ ] Provider operation、cursor、pagination、budget、coverage
- [ ] projection、ContentItem、Evidence、EventCandidate、Fact 完整性
- [ ] Notification、Telegram 与 collection 解耦
- [ ] config、credential、secret、许可和安全边界
- [ ] deterministic code 与 model responsibility
- [ ] tests、failure paths 和环境差异
- [ ] SPEC/README/AI_CONTEXT 等跨文档一致性
- [ ] 当前阶段授权边界及后续阶段记录

不适用的项目必须明确标记 `N/A` 并说明理由，不能静默跳过。

## 5. 问题台账

每轮审核必须维护以下状态：

```text
DISCOVERED
REQUIRED
VERIFIED_FIXED
REMAINS_OPEN
DEFERRED
INTENTIONAL_BOUNDARY
```

规则：

- 问题只有在真实 diff 和相关测试/合同中验证后才能进入 `VERIFIED_FIXED`；
- 新的修改命令必须包含全部 `REMAINS_OPEN`，不得改一个忘一个；
- `DEFERRED` 必须指明所属 readiness step/SPEC 和进入条件；
- `INTENTIONAL_BOUNDARY` 不得被当作缺陷擅自放宽；
- 已修复问题不得因措辞变化被重复提出。

## 6. 集中输出与冻结基线

标准流程只有：

```text
完整审核
→ REQUEST CHANGES — CONSOLIDATED
→ 一次集中修正
→ 对照原清单逐项验收
→ 检查修订是否引入新矛盾
→ PASS 或一次性列出全部剩余项
```

禁止使用：

```text
发现一个问题
→ 立即停止审核
→ 发修改命令
→ 修复后再继续发现相邻问题
```

集中修正清单发出后即形成冻结验收基线。后续只能：

- 验证该清单是否落实；
- 报告新提交本身引入的新矛盾；
- 报告因客观缺失文件或权限在上一轮不可见的问题。

不得继续增加同层级偏好或重新设计已经验收的决定。若漏审旧问题，审核者必须明确记录为漏审，而不能描述成新提交引入的问题。

## 7. 问题分级

- **A / BLOCKER**：不修不能进入下一 Review、实现、migration、production activation 或 AI 接入。
- **B / REQUIRED**：必须修复，但可以纳入已授权实施批次，不阻止当前 docs decision。
- **C / DEFERRED**：当前阶段无需实现，必须记录明确的后续 step/SPEC。
- **BOUNDARY**：有意的安全、许可、市场、credential、内容或交易边界，不应放宽。

## 8. 强制输出证据

最终审核结果必须包含：

- 审核 SHA、目标分支和 merge base；
- 实际检查的文件/模块范围；
- 三遍审核是否完成；
- 审核矩阵的 PASS/N/A/FAIL 摘要；
- 问题台账及 A/B/C/BOUNDARY 分类；
- 是否存在未读取内容或验证限制；
- 明确的下一状态。

允许的审核状态只有：

- `INCOMPLETE REVIEW`
- `REQUEST CHANGES — CONSOLIDATED`
- `PASS — DOCS ONLY`
- `PASS — IMPLEMENTATION`
- `BLOCKED`

不得把单个发现、部分 diff 检查或工具验证通过描述成完整审核 PASS。

## 9. 速度与并行原则

- 可以并行读取相互独立的 Provider、文档和测试范围；
- migration 与合并顺序仍按真实依赖串行；
- R3–R7 的能力审计可以并行准备，但不得越过 Foundation/SPEC 授权实施；
- 不因非阻断问题暂停其他范围的只读审计；
- 审核追求一次完整收口，而不是减少单轮阅读范围。

## 10. 职责边界

- Reviewer 负责发现问题、完成取舍并给出确定合同；
- Codex implementation agent 按已批准合同实施，不自行选择重大架构替代方案；
- Docs Review PASS 不自动授权代码、migration、外部请求、credential 读取或 production activation；
- 只有用户可以授予下一阶段实现或真实外部操作权限。

