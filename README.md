# Market Intelligence Collector

用户已确认本项目的长期产品目标：建立面向个人投资研究的实时信息采集与 AI 分析系统，
提供重要事件的一键了解、可追溯影响分析、市场数据验证和可解释研究参考。

这是产品目标，不是所有未来能力的实现授权。当前生效边界是 Foundation v2.3-FROZEN：完整保留
v2.2 的单用户、自用、直接市场范围、安全、许可与 provenance 边界，并有限授权 R1–R8 分别进入
独立 SPEC/Review；真实 AI、Market Validation 和 Research Recommendation 仍未授权。
Foundation v2.1-FROZEN 的原始安全基线经 v2.2 继承，并继续由 v2.3 完整保留。

系统按四个阶段演进：

1. 稳定采集信息并及时推送；
2. 使用 AI 做事件整合、事实提取和跨市场影响分析；
3. 结合真实持仓与用户确认的投资计划做风险映射和复核提醒；
4. 根据用户明确反馈持续优化，但不根据点击、忽略或浏览行为缩小信息范围。

## 冻结状态

- Foundation：v2.3-FROZEN
- 状态：Frozen
- 当前阶段：Event Intelligence foundation（Phase 1 core path Completed 且继续运行）
- 当前 Active SPEC：SPEC-0041 Implementation — Unified Production Collection Control Plane（R1
  Implementation Review）。R1 Docs Review 已 PASS，I-A、II、III、IV bounded implementation 已明确授权并在
  Draft PR #43 实现；代码包含 inactive multi-target control plane，但 production authority 仍为 `legacy`，
  unified authority 尚未 activation。四 Provider 的 bounded adapter/runtime/scheduler evidence 不代表完整
  production data coverage。
- Foundation v2.3-FROZEN 已通过 [R0 Freeze Review](docs/FOUNDATION_V2_3_FREEZE_REVIEW.md)；R0
  Completed/PASS，但不自动启动 R1，也不授权代码、migration、schema、runtime 或外部请求。完整路线见
  [Pre-AI Collection Readiness Program](docs/PRE_AI_COLLECTION_READINESS.md)。PR #39 保持 Draft。
- 最近完成：[`spec/SPEC-0039-phase1-acceptance-event-candidate-foundation.md`](spec/SPEC-0039-phase1-acceptance-event-candidate-foundation.md) — Completed, Implementation Review approved

Phase 1 固定主链路：

```text
Source Registry
→ Collection
→ Raw Item
→ Deterministic Normalization
→ Content Item
→ Deterministic Deduplication
→ Storage
→ Notification Outbox
→ Telegram Push
→ Operations / Health / Audit
```

Phase 1 历史边界不包含 LLM、AI 摘要或 Event；v2.2 仅新增已审核的 EventCandidate/Evidence
foundation，不授权真实 AI、Portfolio、Holding、Investment Plan、Candidate Rule 或交易建议。

## 核心边界

- 直接 market/portfolio scope：U.S. equities、U.S. ETFs、Crypto 和 related cash positions；宏观、
  能源、监管、债券、FX 与商品仅作为解释性输入，除非未来 Foundation Revision 改变边界。该范围
  不授权 Portfolio/Holding/Investment Plan implementation。
- 第一阶段交互入口：Telegram 管理 Bot 与情报推送 Bot。
- 系统不自动交易，不自动下单，也不输出替用户作出买卖决定的指令。
- AI 不得擅自修改投资计划、风险规则或持仓逻辑。
- 付费和受版权保护内容只在合法授权范围内接入和保存。

## 文档阅读顺序

1. [`AI_CONTEXT.md`](AI_CONTEXT.md)
2. [`docs/FOUNDATION.md`](docs/FOUNDATION.md)
3. [`docs/ROADMAP.md`](docs/ROADMAP.md)
4. [`docs/SYSTEM_DESIGN.md`](docs/SYSTEM_DESIGN.md)
5. [`docs/DATA_MODEL.md`](docs/DATA_MODEL.md)
6. [`docs/AI_RULES.md`](docs/AI_RULES.md)
7. 当前 [`spec/`](spec/) 中的 Active SPEC

来源、术语、历史决策和开发流程分别记录在：

- [`docs/SOURCE_CATALOG.md`](docs/SOURCE_CATALOG.md)
- [`docs/GLOSSARY.md`](docs/GLOSSARY.md)
- [`docs/DECISIONS.md`](docs/DECISIONS.md)
- [`docs/DEVELOPMENT_WORKFLOW.md`](docs/DEVELOPMENT_WORKFLOW.md)

## 当前状态

- Foundation：v2.3-FROZEN
- 当前阶段：Event Intelligence foundation；Phase 1 core path Completed/operational
- Active SPEC：SPEC-0041 Implementation（R1 Implementation Review）。R0 与 R1 Docs Review PASS；
  I-A、II、III、IV bounded implementation 已明确授权，Migration B/production activation 未授权。

统一 runtime verification：

```bash
.venv/bin/python scripts/multi_provider_runtime_smoke.py
.venv/bin/python scripts/multi_provider_runtime_smoke.py --doctor
.venv/bin/python scripts/multi_provider_runtime_smoke.py --bootstrap-target
FINNHUB_API_KEY=... EIA_API_KEY=... SEC_USER_AGENT=... SEC_CONTACT_EMAIL=... \
  .venv/bin/python scripts/multi_provider_runtime_smoke.py --execute
```

默认模式完全 inert；只有 `--execute` 接收 process environment。runner 固定串行执行三家且每家最多
一次请求，不读取 `.env`，不保存 response 或 live output。

SPEC-0036 Provider target 准备（不读 Provider credential、不请求 API）：

```bash
.venv/bin/python scripts/finnhub_ingestion_smoke.py --doctor
.venv/bin/python scripts/finnhub_ingestion_smoke.py --bootstrap-target
.venv/bin/python scripts/eia_ingestion_smoke.py --doctor
.venv/bin/python scripts/eia_ingestion_smoke.py --bootstrap-target
.venv/bin/python scripts/sec_edgar_ingestion_smoke.py --doctor
.venv/bin/python scripts/sec_edgar_ingestion_smoke.py --bootstrap-target
```

Bootstrap 只创建最小 enabled/authorized Source + SourceAccount，重复执行为 `already_exists`；多个
eligible targets 会 fail closed。API key、SEC User-Agent/contact 不写入 DB。
- 最近完成：[`spec/SPEC-0003.md`](spec/SPEC-0003.md)，tag `spec-0003-completed`
- NewsAPI.ai / Event Registry：future / blocked；GDELT：runtime blocked / future evaluation only
- SPEC-0019 pure contract scaffold 与 SPEC-0020 provider mapping scaffold 已 Completed；SPEC-0021
  schema design 与 `evidence_items` migration、ORM、PostgreSQL schema tests implementation 已
  Completed，SPEC-0023 Write Path、SPEC-0024 Adapter Integration Docs Review 与 SPEC-0025 Adapter
  Scaffold、SPEC-0026 Collection Runner mocked integration 与 SPEC-0027 RawItem-to-Evidence
  orchestration、SPEC-0028 projection trigger 与 SPEC-0029 mock E2E implementation 也已 Completed。
  SPEC-0030–0038 已完成 real adapters、bounded runtime、collection/evidence/feed、四 Provider cadence
  scheduler 与 Telegram routing。SPEC-0039 Phase 1 acceptance、Foundation v2.2 transition 与 Event
  Candidate Foundation 已完成并通过 Implementation Review。SPEC-0022 已被 SPEC-0039 absorb/supersede。
- SPEC-0005 仍为 X Source and Account Collection Planned 范围，不由当前 SPEC 改写

## 长期产品与架构方向

已确认的产品目标是“一键了解重要事件 → AI 事实与影响分析 → 市场数据验证 → 可解释研究参考”，而不是普通新闻列表或对外转载平台。供应商无关混合采集、统一逻辑新闻记录、事件驱动处理与回补能力见 `docs/SYSTEM_DESIGN.md` 和 `docs/DATA_MODEL.md`。

产品目标已经确认，但相关工程能力不代表均已实现或获准。Foundation v2.3-FROZEN 继承并保留
deterministic EventCandidate foundation；真实 AI 分析、Market Validation、Research Recommendation、
多用户、商品直接投资域和交易动作语义仍须独立 SPEC/Review。v2.1 的禁止自动交易、合法授权
和 Phase 1 运行边界继续生效。

## 目录

```text
.
├── alembic/                  # 数据库迁移
├── src/market_intelligence/  # API、配置、数据库与任务入口
├── tests/                    # 单元与基础集成测试
├── compose.yaml
├── Dockerfile
├── pyproject.toml
├── uv.lock
├── README.md
├── AI_CONTEXT.md
├── docs/
├── spec/
└── scripts/
```

该仓库中的 Markdown 文档是项目设计基线；代码、迁移、测试和交付报告描述当前实现事实。冻结规则见 `FOUNDATION_FROZEN.md`，Phase 1 最终验收见 `docs/PHASE1_ACCEPTANCE.md`。

## 环境要求

- Python 3.12
- [uv](https://docs.astral.sh/uv/) 0.8 或兼容版本
- Docker Desktop 或支持 Compose v2 的 Docker Engine

真实配置只写入未跟踪的 `.env`。首次使用：

```bash
cp .env.example .env
uv sync --frozen
```

`.env.example` 只包含本地开发示例值，禁止用于生产。生产环境必须显式提供所有服务 URL，不能使用示例凭据或本地主机地址。

## 本地启动

### Marketaux bounded live smoke

在已激活项目 Python 3.12 `.venv` 后，默认命令是安全 dry-run：不读取 token、不请求 API，只输出
redacted plan。未激活环境时使用 `.venv/bin/python` 等价执行。

```bash
python3 scripts/marketaux_live_smoke.py
# 未激活 .venv 时：.venv/bin/python scripts/marketaux_live_smoke.py
```

只有用户另行明确授权后，才可手动通过 process environment 执行一次 bounded smoke：

```bash
MARKETAUX_API_TOKEN=... python3 scripts/marketaux_live_smoke.py --execute --limit 1
```

limit 默认 1、最大 3。脚本不读取 `.env`，不保存 response，不输出 title/body/URL/snippet/
description、完整 request URL 或 token，也不写 DB/evidence_items。CI、pytest、package review 和默认命令
均不得传 `--execute`。

### Marketaux real collection pipeline

默认 dry-run 不读取 token、不连接 DB/Redis、不请求 API，也不写任何数据：

```bash
python3 scripts/marketaux_real_collection_smoke.py
```

首次在干净数据库运行时，先用 doctor 检查并用幂等 bootstrap 建立最小 metadata-only target；两者
都不读取 token、不请求 Marketaux API：

```bash
.venv/bin/python scripts/marketaux_real_collection_smoke.py --doctor
.venv/bin/python scripts/marketaux_real_collection_smoke.py --bootstrap-target
```

bootstrap 返回 `created` 或 `already_exists` 后，只有用户明确授权，且 doctor 确认恰好一个
enabled/authorized Marketaux SourceAccount，才可手动执行一次 limit 1–3 的真实 pipeline：

```bash
MARKETAUX_API_TOKEN=... python3 scripts/marketaux_real_collection_smoke.py --execute --limit 1
```

执行路径复用既有 runner、RawItem transaction、EvidencePipelineService 与 EvidenceWriteService，并在
同一运行的 sanitized sidecar 中保留允许展示的 title/public URL，以 metadata-only ContentItem 持久化；
不保存 response/body/snippet/description。CI、pytest、package review 不执行 `--execute`。

### Marketaux visible feed and manual Telegram

读取最近 10 条已持久化、metadata-only Marketaux 可见新闻（只读 DB，不请求 Provider）：

```bash
python3 scripts/marketaux_feed_smoke.py --limit 10
```

本地验收需要至少一条可见新闻时，使用 fail-closed 模式：

```bash
python3 scripts/marketaux_feed_smoke.py --limit 3 --require-items
```

默认 read-only 模式允许空 feed；`--require-items` 在空 feed 时返回
`BLOCKED` / `visible_feed_empty`。Marketaux display projection 与 RawItem/evidence metadata contract
严格分离，title/public URL 不进入 content-free evidence metadata。

默认 Telegram preview 读取最近新闻并生成仅含标题、来源、时间、链接的消息；不读取 Telegram token，
也不请求 Telegram：

```bash
python3 scripts/telegram_marketaux_push_smoke.py --limit 3
```

只有用户明确授权时才可显式手动推送，默认 3 条、最大 5 条：

```bash
TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... \
  python3 scripts/telegram_marketaux_push_smoke.py --execute --limit 3
```

凭证只从 process environment 读取，脚本强制不读 `.env`；不输出 token/chat id，不保存 Telegram
response，不自动循环或调度。execute 也先查 feed；空 feed 时不读 token/chat id
且不发送请求。SPEC-0033 生效前已保存的 content-free RawItem 不进行猜测性回填。

### Alembic state doctor and guarded repair

持久化 Docker 数据库出现 unknown revision 时，不要执行 `docker compose down -v`，也不要跳过
migrate。先确保运行的是当前代码构建的 image，然后执行只读 doctor 与 repair dry-run：

```bash
docker compose build api migrate
docker compose run --rm --no-deps api uv run python scripts/alembic_state_doctor.py
docker compose run --rm --no-deps api uv run python scripts/alembic_state_repair.py
```

只有 doctor 明确返回 `repair_available=true`，并确认 schema 与当前唯一 head 完全兼容时，才可人工执行：

```bash
docker compose run --rm --no-deps api uv run python scripts/alembic_state_repair.py --execute
```

当前 code head 为 reconciliation revision `0004`：它用于修复曾应用早期 `0003` artifact 的数据库，
补齐 composite provenance FK/index 与 secret-marker check。若 DB revision 是代码认识的 `0003`，应使用
正常 migrate 而不是 repair。repair 只能将未知 bookkeeping revision 修到当前 code head，不接受任意目标，
也不执行 DDL。随后仍须运行正常 migrate 并验证 api：

```bash
docker compose run --rm migrate
docker compose up -d api
docker compose ps api
```

### Minimal Marketaux Telegram scheduler

默认 smoke 是完全 inert 的 dry-run，不读取任何 Provider/Telegram credential、不连接 runtime、不发送：

```bash
python3 scripts/marketaux_telegram_scheduler_smoke.py
```

只有用户明确授权的一次 manual cycle 才使用 `--execute`。limit 默认 1、最大 3：

```bash
MARKETAUX_API_TOKEN=... TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... \
  python3 scripts/marketaux_telegram_scheduler_smoke.py --execute --limit 1
```

Celery Beat 注册 `marketaux.telegram.run`，默认 `MARKETAUX_TELEGRAM_SCHEDULER_EXECUTE=false`，因此
dry-run 不读取 credential 或访问 runtime。自动投递使用 Notification dedup key；SENT 永久去重，FAILED
最多重试 3 次，超过 300 秒的 stale SENDING 可被原子恢复。历史 retry 独立于当前 collection run，即使
本轮没有新 item 或 Provider 失败也可继续安全投递。
明确启用 worker runtime 时，凭证只来自 process environment；Notification 不会被删除来绕过去重。

### Multi-provider scheduler + Telegram routing

SPEC-0038 的统一 smoke 默认完全 inert，不读取 Provider/Telegram credential、不连接 runtime：

```bash
python3 scripts/multi_provider_scheduler_smoke.py
```

只有显式启用时才从 process environment 读取四 Provider 与 Telegram credential：

```bash
MARKETAUX_API_TOKEN=... FINNHUB_API_KEY=... EIA_API_KEY=... \
SEC_USER_AGENT=... SEC_CONTACT_EMAIL=... \
TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... \
python3 scripts/multi_provider_scheduler_smoke.py --execute --limit 1
```

Celery Beat 注册 `multi_provider.telegram.run`，默认
`MULTI_PROVIDER_SCHEDULER_EXECUTE=false`。四 Provider 使用独立 cadence；单家失败隔离，
no-new-items 不发送。Notification unique dedup key 保证 SENT 不重发，FAILED bounded retry 和 stale
SENDING recovery 独立于当前 collection cycle。Finnhub/EIA 展示只含 content-safe metadata，不含
quote/EIA numeric value；SEC 不下载 filing body。

Telegram credential 缺失不会停止 collection：四家仍可写 RawItem/EvidenceItem/ContentItem，summary
保留各自 `collection_status` 并将 `delivery_status` 标记为 `BLOCKED`。新 ContentItem 的 Notification
保持 PENDING，既有 FAILED/SENDING 不被消费；credential 恢复后再由原子 claim 发送。Provider 的
transient RETRY 使用 CollectionRunner 已计算的 bounded delay 和独立 Redis retry gate，不必等待完整
正常 cadence；retry 成功后恢复正常 cadence，non-retryable failure 不快速重试。

EIA monthly 与 SEC submissions 是 snapshot polling：重复返回当前最新 cursor 时正常结束为
no-new-items，不重复写 RawItem/EvidenceItem/ContentItem/Notification，也不推进 cursor/watermark；只有
newer cursor 才写入，older cursor fail closed。Marketaux/Finnhub 继续要求 strict successor。

先启动依赖服务：

```bash
docker compose up -d postgres redis
uv run alembic upgrade head
uv run uvicorn market_intelligence.main:app --host 127.0.0.1 --port 8000
```

另开终端启动 worker 和只处理 fake adapter 的 Celery Beat：

```bash
uv run celery -A market_intelligence.tasks.celery_app:celery_app worker --loglevel=INFO
uv run celery -A market_intelligence.tasks.celery_app:celery_app beat --loglevel=INFO
```

这是本地 uv 路线：Compose 将 PostgreSQL 暴露到 `${POSTGRES_PORT:-5432}`，将 Redis 暴露到 `${REDIS_PORT:-6379}`；`.env.example` 因此使用 `localhost`。完整 Compose 路线会通过服务级 `environment` 自动改用容器内部主机名 `postgres` 和 `redis`，无需修改 `.env`。

健康检查：

```bash
curl http://localhost:8000/health/live
curl http://localhost:8000/health/ready
uv run celery -A market_intelligence.tasks.celery_app:celery_app call system.health_ping
```

`live` 只检查进程；`ready` 同时检查 PostgreSQL 与 Redis。失败响应只包含 `DB_UNAVAILABLE` 或 `REDIS_UNAVAILABLE` 等稳定错误码，不包含连接串。

## Docker Compose

启动完整栈（迁移、API、worker、beat、PostgreSQL 16、Redis 7）：

```bash
docker compose up -d --build
docker compose ps
curl http://localhost:8000/health/ready
```

查看日志或停止服务：

```bash
docker compose logs api worker beat
docker compose down
```

保留命名卷是默认行为。只有明确确认不需要本地数据时，才使用 `docker compose down --volumes`。

## 数据库迁移与回滚

```bash
uv run alembic upgrade head
uv run alembic current
uv run alembic downgrade -1
uv run alembic upgrade head
```

回滚会修改本地数据库 schema；执行前确认目标数据库并备份任何需要保留的数据。SPEC-0001 仅创建可回滚的 `system_metadata` 基础设施表。

## 测试与质量检查

```bash
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
docker compose config
python scripts/validate-foundation.py
bash scripts/package-review.sh
```

Review ZIP 默认生成在项目目录外，并排除 `.env`、虚拟环境、IDE 文件、数据库、备份、缓存和日志。

## 常见问题

- `uv: command not found`：安装 uv 后重新运行 `uv sync --frozen`；不要创建第二套锁文件。
- `Cannot connect to the Docker daemon`：启动 Docker Desktop，确认 `docker info` 成功后重试。
- `DB_UNAVAILABLE`：确认 PostgreSQL 已健康、`DATABASE_URL` 使用 `postgresql+asyncpg://`，且主机名与运行方式匹配（本地使用 `localhost`，Compose 内自动使用 `postgres`）。
- `REDIS_UNAVAILABLE`：确认 Redis 已健康，并检查 `REDIS_URL`；本地与 Compose 的主机名同样不同。
- 端口占用：在 `.env` 中修改 `APP_PORT`，或停止占用 8000 端口的进程。
- 迁移连接失败：先运行 `docker compose up -d postgres`，再检查 `.env` 是否指向 `localhost:${POSTGRES_PORT:-5432}`；也可使用容器命令 `docker compose run --rm migrate`。
