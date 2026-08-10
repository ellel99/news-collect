# SPEC-0034 — Alembic Migration State Repair / Docker Startup Health

Status：Completed — Implementation Review approved

Phase：Phase 1 — Operations Safety

Foundation：v2.1-FROZEN（unchanged）

Depends on：SPEC-0033（Completed）

## 1. 目标

提供安全的 Alembic code/database state doctor 与严格受保护的 head-only repair，使持久化 Docker
数据库遇到未知 revision 时不删除 volume、不跳过 migration，并能恢复可审计的启动路径。

## 2. 已知事实与诊断

- 用户的既有数据库报告 `alembic_version=0003`，旧 migrate container 报无法定位 `0003`。
- Git 历史确认 `0003` 首次交付后曾被原地增强；用户数据库应用的是旧 artifact，版本表虽为
  `0003`，但仍是单列 RawItem FK，并缺少 composite provenance index/FK 与 secret-marker check。
- stale Docker image 同时解释了运行中的 migrate 无法定位代码已存在的 `0003`。本 SPEC 不改写
  `0003`，而新增幂等 forward reconciliation revision `0004`，并要求 rebuild current image。
- 不通过 `docker compose down -v`、删除 volume、跳过 migrate 或伪造 stamp 解决。

## 3. 范围

- `alembic_state_doctor.py`：只读数据库 revision、代码 revisions/head、revision chain 与 schema/head
  compatibility，输出不含连接串的 safe JSON。
- `alembic_state_repair.py`：默认 dry-run；只有未知数据库 revision、单一且完整 code head、schema
  与 metadata 无差异时，`--execute` 才可把版本表更新到当前 code head。
- repair 不接受任意 revision 参数；事务内 advisory lock，并以原 revision 条件更新，防止并发漂移。
- Docker 恢复流程必须 rebuild current image、先 doctor、正常运行 migrate，再启动/检查 api。

## 4. 非范围

- 不删除、重命名、squash 或再次改写既有 migration；新增 `0004` 仅向前补齐当前 ORM/metadata
  已要求但旧 `0003` artifact 未创建的约束，不新增 table/column/entity。
- 不删除数据库或 volume，不提交 dump，不读取 `.env`，不输出连接串或 secret。
- 不跳过 migrate，不把 doctor/repair 接成自动启动时的隐式 stamp。
- 无 scheduler、AI、投资建议、dedup/Event、多 Provider 或 SPEC-0022。

## 5. Doctor contract

输出只包含 status、数据库 revision、code heads/revisions、known/chain/schema booleans、schema diff
数量、repair availability 与固定 safe errors。下列状态 fail closed：

- version table/revision 缺失；
- DB revision 不在当前代码；
- revision chain 断裂或多 head；
- schema 与当前 metadata 存在差异；
- 数据库连接或 inspection 失败。

## 6. Repair contract

- 已在 head：PASS，不更新。
- 已知旧 revision（例如 `0003`）且 code head 为 `0004`：使用正常 `alembic upgrade head`，不得 repair。
- 未知 revision + schema compatible + 单一完整 head：dry-run 返回 `DRY_RUN` 与
  `alembic_repair_requires_execute`，数据库不变。
- 相同前置条件下显式 `--execute`：只更新到当前 head，返回 `REPAIRED`。
- 其他情况：BLOCKED，数据库不变；不得猜测或自动 fallback。

该 repair 是异常状态恢复工具，不替代 Alembic migration，不应放入正常 migrate service。

## 7. Docker recovery runbook

```bash
docker compose build api migrate
docker compose run --rm --no-deps api uv run python scripts/alembic_state_doctor.py
docker compose run --rm --no-deps api uv run python scripts/alembic_state_repair.py
```

只有 doctor 明确 `repair_available=true` 且人工审核 schema compatibility 后：

```bash
docker compose run --rm --no-deps api uv run python scripts/alembic_state_repair.py --execute
```

随后必须走正常 migration/startup：

```bash
docker compose run --rm migrate
docker compose up -d api
docker compose ps api
```

新 image 中 `database_revision=0003` 是已知 prior revision，doctor 应报告 normal upgrade available；
不执行 repair，直接通过正常 migrate 应用 `0004`。迁移会先检查 unsafe reference 与 provenance mismatch，
发现任一问题即 fail closed 且不输出数据值。

## 8. 测试与验收

- [x] 当前 repository revision chain 为 `0001 -> 0002 -> 0003 -> 0004`、单一 head `0004`。
- [x] `0004` 对旧 `0003` 补齐 composite provenance index/FK 与 secret-marker check；对当前完整
  `0003` 幂等 no-op。
- [x] known head + compatible schema 为 PASS。
- [x] code 不认识 `0003` 时返回 `alembic_database_revision_missing_from_code`。
- [x] dry-run 不修改数据库。
- [x] compatible schema 才允许显式 head-only repair。
- [x] incompatible schema/blocking chain 不修改数据库。
- [x] safe output 不包含连接串/password/token/`.env`。
- [x] 无外部 API、Telegram/Provider credential 或业务 pipeline dependency。
- [x] CI、Docker doctor/migrate/api startup 与 Reviewer PASS。

## 9. 数据模型与 migration

新增 reconciliation migration `0004`，不修改 ORM、不新增 table/column/entity。它只补齐当前
`0003` 文件和 metadata 已定义、但旧 artifact 数据库缺失的安全约束。downgrade 保留这些当前
`0003` contract 约束，避免重新引入已知 drift。显式 repair 只在未知 revision 且 guardrails 全部
通过时修复 `alembic_version.version_num` bookkeeping；它不执行 DDL。

## 10. Review History

| Round | Result | Evidence | Resolution |
|---|---|---|---|
| 1 | IN REVIEW | implementation diff、PostgreSQL tests、Docker safe summaries、review package | 等待用户/ChatGPT Review |
| 2 | PASS | PR #33 CI 与 ChatGPT review；Docker `0003 -> 0004`、doctor、downgrade/re-upgrade、api healthy | PR #33 合并为 `089d62b` |
