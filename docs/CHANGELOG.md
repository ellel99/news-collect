# Changelog

## Foundation v2.1-FROZEN — 2026-07-28

### Frozen

- 单用户、美股/ETF/Crypto、Broad Scan、Controlled Push、四阶段边界、自动交易禁止和 Phase 1 技术基线。

### Changed

- Phase 1 收敛为采集、原始留痕、确定性标准化、确定性去重、存储、Outbox 和 Telegram 推送；
- Phase 1 改为 Content First，Phase 2 起 Event First；
- AI/Event/Evidence 移至 Phase 2；Portfolio/Investment Plan 移至 Phase 3；
- P0–P4 改为 Phase 1 可执行的确定性规则；
- 管理 Bot 收缩为运维管理；
- Source Catalog 增加实现契约；
- uv 固定为依赖与锁文件工具。

### Added

- FOUNDATION_FROZEN.md；
- docs/PHASE1_ACCEPTANCE.md；
- docs/FREEZE_REVIEW.md；
- spec/SPEC_INDEX.md；
- scripts/validate-foundation.py；
- MANIFEST.sha256。

## Foundation v2.0 — 2026-07-28

### Added

- 单用户、美股与 Crypto 的正式项目边界；
- Collection、Analysis、Notification、Portfolio 四个 Scope；
- Broad Scan 与 Controlled Push；
- P0–P4 通知定义；
- Content Item、Event、Evidence、Investment Plan 等统一术语；
- Source Catalog 与稳定账号 ID 验证要求；
- 核心决策记录；
- 可审计的 SPEC 和 Delivery Report 模板；
- Phase 1 可执行技术基线；
- 安全打包与秘密扫描脚本。

### Changed

- Phase 3 改为持仓影响、组合风险和投资计划复核；
- 删除 AI 替用户选择买卖、加减仓等交易动作的权限；
- 长期优化改为显式反馈和用户确认；
- Telegram 被定义为主要入口而非整个服务端后台；
- 原始 Content Item 与 Event 分层保存；
- 项目信息源只保存当前有效知识，Git 保存历史。

### Removed

- 多用户、Workspace、团队和 SaaS；
- 隐式点击、打开、忽略行为学习；
- 自动缩小信息覆盖；
- 自动修改投资计划；
- 自动交易和确定性投资指令；
- 以未授权方式获取或保存付费正文。
