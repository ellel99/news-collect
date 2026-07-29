# Delivery Report

Build：SPEC-0003 Collection Framework, Scheduler, Cursor and Retry
Active SPEC：SPEC-0003
交付日期：2026-07-29
审核状态：IN REVIEW

## 1. 本轮目标

在不接入真实来源、不修改 schema 的前提下，实现来源无关采集 contract、fake adapter、调度与运行任务、cursor checkpoint、retry、错误分类和 stale recovery。

## 2. Foundation 与范围

- Foundation：v2.1-FROZEN
- 当前阶段：Phase 1 — Information Collection & Push
- 是否修改 Foundation：否
- 是否接入真实来源：否
- 是否创建未来阶段实体：否
- 是否修改 ORM 或 schema：否
- Alembic revision：无新增

## 3. 已实现

- 不可变 `CollectionTarget`、`CursorSnapshot`、`FetchRequest`、`RawItemEnvelope`、`FetchBatch`
- 异步 `CollectionAdapter` protocol 与 `ClassifiedCollectionError`
- 仅允许 `Source.access_method = "fake"` 的 `AdapterRegistry`
- fake adapter 使用纯合成数据且不访问网络
- `authorized` / `implemented` + enabled + due 的 dispatcher 门禁
- 确定性 dispatch key 与 Celery task ID
- `collection.dispatch_due_targets`
- `collection.run_target`
- `collection.recover_stale_runs`
- Redis owner-token lock、续期、错误 owner 防护和安全释放
- CollectionRun running/succeeded/partial/failed 生命周期与 retry 复用 run ID
- RawItem batch persistence；`new_count`、`duplicate_count` 保持 0
- RawItem 与 cursor 同一 PostgreSQL 事务 checkpoint
- cursor 单调性、stale snapshot 与发布时间水位校验
- timeout、Retry-After、capped exponential full jitter 和最大 retry
- FR-13 全部错误码，包括 `COLLECTION_STALE_RUN`
- source health 成功复位、终态失败递增与 scheduler failure backoff
- stale run 恢复为 failed，不推进 cursor
- Celery Beat dispatcher 与 stale recovery schedule

## 4. 明确未实现

- 真实新闻、RSS、API、网页、X 或 Telegram 来源
- HTTP、feedparser、浏览器、Cookie、登录或 parser
- Source/SourceAccount API、CLI、service/repository CRUD
- 新表、字段、enum、索引、外键、ORM 修改或 Alembic revision
- ContentItem 标准化、URL canonicalization、hash 或去重
- Notification / Outbox 行为
- Telegram Bot
- AI/LLM、Event、Evidence、Analysis
- Portfolio、Holding、Investment Plan、Candidate Rule

## 5. 内部接口与任务

| 类型 | 名称 |
|---|---|
| Contract | `CollectionAdapter`, `CollectionTarget`, `CursorSnapshot`, `FetchRequest` |
| Contract | `RawItemEnvelope`, `FetchBatch`, `ClassifiedCollectionError` |
| Framework | `AdapterRegistry`, `RetryPolicy`, `TargetLock`, `CollectionRunner` |
| Task | `collection.dispatch_due_targets` |
| Task | `collection.run_target` |
| Task | `collection.recover_stale_runs` |

## 6. 数据模型与迁移

- 数据模型变化：无
- 新 Alembic revision：无
- 既有 revision：`0001`、`0002`
- 预期表集合：`alembic_version`、`system_metadata` 和九个 Phase 1 业务表
- downgrade `0002 → 0001` 会按既有 SPEC-0002 契约移除九张业务表；re-upgrade 恢复

## 7. 测试覆盖

- contract 不可变与 nullable 语义
- fake adapter 在 socket 被禁止时仍正常执行
- registry 仅允许 fake；unknown access method fail closed
- FR-13 全错误码 retry 分类
- retry full jitter、Retry-After 和耗尽
- dispatch identity、due time 和 failure backoff
- 七种 authorization status 门禁
- owner-token lock 竞争、续期、错误 owner 和释放
- RawItem/cursor 多页原子 checkpoint
- retry 复用 CollectionRun ID
- 非法 cursor 故障注入不留下 RawItem/cursor
- stale recovery 写入 `COLLECTION_STALE_RUN`、标记 failed 且 cursor 不变
- 已排队 retry 使用 Redis marker，stale recovery 不会误判有效 retry
- Celery task/Beat 注册与 eager contract

## 8. 验证结果

| 检查 | 命令 | 结果 |
|---|---|---|
| 依赖 | `uv sync --frozen` | PASS；57 packages audited |
| Ruff | `uv run ruff check .` | PASS |
| Format | `uv run ruff format --check .` | PASS；60 files |
| mypy | `uv run mypy src` | PASS；24 source files |
| pytest | `uv run pytest` | PASS；52 tests |
| Compose config | `docker compose config` | PASS |
| Services | `docker compose up -d postgres redis` | PASS；PostgreSQL 16 / Redis 7 running |
| Schema drift | `uv run alembic check` | PASS；No new upgrade operations detected |
| Migration upgrade | `uv run alembic upgrade head` | PASS |
| Migration downgrade | `uv run alembic downgrade -1` | PASS；`0002 → 0001` |
| Migration re-upgrade | `uv run alembic upgrade head` | PASS；`0001 → 0002` |
| Schema allowlist | PostgreSQL `pg_tables` 查询 | PASS；仅 11 张预期表 |
| Celery runtime | Compose build/up + worker/beat logs | PASS；三个 collection task 注册，worker ready |
| Foundation | `python3 scripts/validate-foundation.py` | PASS；20 required files，links/freeze markers valid |
| Review package | `bash scripts/package-review.sh` | PASS；73 files，秘密扫描无发现 |

本机全局没有 `uv` 命令；复用 `/tmp/news_collect_uv/bin/uv`，并将 uv cache/Python 安装目录放在 `/tmp`。所有表中列出的 `uv ...` 命令均以该临时 PATH 执行，仓库的 `uv.lock` 未变化。

首次运行 `ruff format --check` 时发现 `tests/test_tasks.py` 一处排版差异并返回非零；执行 Ruff formatter 修复后，重新运行 Ruff、format、mypy 与全部测试均通过。本报告没有把首次失败写成 PASS。

首次构建 worker/beat 时，Compose 中的 `uv run` 尝试运行期同步开发依赖。已将容器命令改为 `uv run --no-sync`；重新构建后 worker/beat 正常连接 Redis，三个 collection task 均注册。

## 9. 安全

- 无真实 `.env`、Token、Cookie、私钥、数据库或备份
- fake adapter 不包含网络 client 或真实 endpoint
- collection options、错误和日志不得包含秘密或 raw payload
- Review ZIP 秘密扫描结果见最终验证

## 10. 已知问题

### Blocker

- 无已知 blocker；等待实现审核。

### Must Fix

- 无已知 must-fix；等待实现审核。

### Future Scope

- 真实来源属于 SPEC-0004/0005。
- Normalization/Dedup/Outbox 属于 SPEC-0006。
- Telegram 属于 SPEC-0007/0008。
- AI/Event/Portfolio 属于后续阶段。

## 11. Git

分支：`feat/spec-0003-collection-framework-scheduler-cursor-retry`

提交信息：`feat: implement SPEC-0003 collection framework scheduler cursor retry`

## 12. 下一步

完成全部验证、提交并创建 PR；等待 SPEC-0003 实现审核，不开始 SPEC-0004。
