# Market Intelligence Collector

用户已确认本项目的长期产品目标：建立面向个人投资研究的实时信息采集与 AI 分析系统，
提供重要事件的一键了解、可追溯影响分析、市场数据验证和可解释研究参考。

这是产品目标，不是当前实现状态或实现授权。当前生效边界仍是 Foundation v2.1-FROZEN：
单用户、自用、美股/ETF/Crypto，且 Phase 1 禁止 AI、Event、Market Validation 和 Research
Recommendation。

系统按四个阶段演进：

1. 稳定采集信息并及时推送；
2. 使用 AI 做事件整合、事实提取和跨市场影响分析；
3. 结合真实持仓与用户确认的投资计划做风险映射和复核提醒；
4. 根据用户明确反馈持续优化，但不根据点击、忽略或浏览行为缩小信息范围。

## 冻结状态

- Foundation：v2.1-FROZEN
- 状态：Frozen
- 当前阶段：Phase 1 — Information Collection & Push
- 开发入口：[`spec/SPEC-0004.md`](spec/SPEC-0004.md)，当前仅 Docs Review，implementation not started

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

Phase 1 不包含 LLM、AI 摘要、Event、Evidence、Portfolio、Holding、Investment Plan、Candidate Rule 或交易建议。

## 核心边界

- 覆盖市场：美股、ETF、Crypto，以及解释二者所需的宏观、政策、AI 产业链和能源信息。
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

- Foundation：v2.1-FROZEN
- 当前阶段：Phase 1 — Information Collection & Push
- Active SPEC：[`spec/SPEC-0004.md`](spec/SPEC-0004.md) — Docs Review
- 最近完成：[`spec/SPEC-0003.md`](spec/SPEC-0003.md)，tag `spec-0003-completed`
- 当前首个 bounded smoke candidate：Marketaux；执行后必须停止并等待用户/ChatGPT Review
- 当前顺序：Marketaux → Finnhub → EIA Open Data → SEC EDGAR
- NewsAPI.ai / Event Registry：future / blocked；GDELT：runtime blocked / future evaluation only
- bounded smoke PASS 前不得 implementation

## 长期产品与架构方向

已确认的产品目标是“一键了解重要事件 → AI 事实与影响分析 → 市场数据验证 → 可解释研究参考”，而不是普通新闻列表或对外转载平台。供应商无关混合采集、统一逻辑新闻记录、事件驱动处理与回补能力见 `docs/SYSTEM_DESIGN.md` 和 `docs/DATA_MODEL.md`。

产品目标已经确认，但相关工程能力不代表已实现或已获准实现。AI 分析、Event、Market
Validation、Research Recommendation、多用户、商品直接投资域和交易动作语义，必须分别
完成 Foundation revision、Freeze Review 和独立 SPEC；当前 v2.1-FROZEN 的 Phase 1、
禁止自动交易和合法授权边界继续生效。

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
