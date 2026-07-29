# Development Workflow

版本：2.1-FROZEN  
状态：Stable

## 1. 三种载体

- Project Knowledge：仅保存当前有效文档、最新审核包、Active SPEC 和最近交付报告；
- Git：保存代码与文档历史、Diff、Commit 和回滚；
- Release ZIP：保存每次交付物。

不得在 Project Knowledge 同时保留多个状态不明的 `final`、`latest` 或重复版本。

## 2. Foundation 冻结规则

普通开发可以更新 SPEC、代码、Migration、测试、Source 状态、Delivery Report 和实现 Changelog；不得顺带改变项目目标、四阶段边界、Phase 1 禁止项、Broad Scan、自动交易边界或核心技术栈。需要改变时先新增 Decision 并由用户明确确认新 Foundation 版本。

## 3. 功能闭环

```text
读取当前资料与代码
→ 确认唯一 Active SPEC
→ 实现
→ 自动测试与手动验证
→ 交付完整 ZIP
→ 用户导入 IDEA 并提交 Git
→ 用户生成 Review ZIP
→ 审核
→ 修复并重复
→ PASS
→ 标记 SPEC Completed
→ 开启下一 SPEC
```

## 4. 开工检查

AI 必须：

1. 读取 `AI_CONTEXT.md` 指定的文档；
2. 检查目录、依赖、配置、迁移和测试；
3. 读取最新 Delivery Report 和 Active SPEC；
4. 查看 Git 状态或用户提供的完整 Review ZIP；
5. 确认没有秘密或本地数据进入工作范围；
6. 将 SPEC Tasks 与当前实现逐项映射。

## 5. 实现规则

- 只修改 Active SPEC 范围；
- Phase 1 不引入 AI、Event、Evidence、Portfolio、Holding 或 Investment Plan；
- 保留用户已有更改，避免无关重构；
- 新行为必须有测试；
- 数据库变化必须使用迁移；
- 外部服务必须可通过配置替换并有失败测试；
- API、任务和通知必须幂等；
- 所有错误必须可分类、可记录、可重试或明确不可重试；
- 不提交真实 `.env`、Token、Cookie、数据库或备份；
- 对被授权但当前无法访问的外部系统，使用合约测试，不伪造已完成的真实集成。

## 6. 验证层级

至少按风险执行：

1. 静态检查：Ruff、mypy；
2. 单元测试；
3. 数据库迁移 up/down 或等价回滚验证；
4. 集成测试；
5. Docker Compose 启动和健康检查；
6. 当前 SPEC 的手动端到端步骤；
7. 打包安全检查。

测试未运行必须写明原因，不能写“应当通过”。

## 7. 交付包

完整交付 ZIP 包含：

- 源代码；
- 依赖锁定文件；
- 迁移；
- 配置模板；
- 测试；
- 文档；
- Active SPEC；
- `DELIVERY_REPORT.md`；
- `CHANGELOG.md`；
- 打包脚本。

不包含：

- `.git`、`.idea`；
- 虚拟环境和依赖缓存；
- 构建产物；
- 日志和临时文件；
- 真实 `.env`；
- Token、Cookie、私钥；
- 本地数据库、运行时数据、备份；
- 未授权正文或个人导出。

## 8. Git 规则

每个功能独立提交；审核修复另建提交。

建议：

```text
feat: implement SPEC-0001 project bootstrap
fix: address SPEC-0001 review findings
docs: finalize SPEC-0001 delivery
```

不要删除 Git 历史或用新 ZIP 重建仓库。

## 9. 审核结果

- `PASS`：所有验收标准满足，无阻塞问题；
- `PASS WITH ISSUES`：功能可接受，只有不阻塞的后续项；
- `FAIL`：存在需求偏差、安全问题、关键测试失败或功能不完整。

问题分为：

- Blocker；
- Must Fix；
- Improvement；
- Future Scope。

只有 PASS，或用户明确接受 PASS WITH ISSUES，SPEC 才能 Completed。

## 10. 文档同步

- 业务边界变化：更新 Foundation、AI Rules、Decisions；
- 架构变化：更新 System Design；
- 模型变化：更新 Data Model 和迁移；
- 来源变化：更新 Source Catalog；
- 进度变化：更新 Roadmap、SPEC 和 Changelog；
- 每次交付：生成 Delivery Report。

文档不能声称未验证功能已完成。

## 11. 用户更新流程

1. 解压交付包并在 IDEA 中打开；
2. 阅读 Delivery Report；
3. 配置本地 `.env`，不要覆盖 `.env.example`；
4. 运行验证命令；
5. 检查 Git diff；
6. 提交 Git；
7. 运行 `bash scripts/package-review.sh`；
8. 上传 Review ZIP；
9. 等待审核或修复包；
10. 当前 SPEC PASS 后再开始下一项。

## 12. Foundation 候选变更流程

当新的产品或架构要求与当前冻结 Foundation 一致时，可先记录为 Proposed Decision，并在
后续 SPEC 中按正常流程实现。当要求与冻结决策冲突，或会改变阶段边界、数据所有权、安全
边界、许可策略、投资动作语义时，必须遵循以下顺序：

1. 记录候选要求、冲突项、影响范围与待决问题。
2. 形成下一 Foundation 版本草案，不修改当前冻结标记的效力。
3. 完成 Freeze Review，并获得用户明确批准。
4. 更新 Foundation 版本及对应冻结文件。
5. 创建且激活一个实现 SPEC，之后才允许编写代码或迁移。

仓库可以处于“无 Active SPEC”状态。此时只能进行经授权的文档准备、审查或历史收尾，
不得把候选路线图、Proposed Decision 或接口预留解释为实现授权。
