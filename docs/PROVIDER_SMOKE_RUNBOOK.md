# Provider Smoke Runbook

## Gate

执行顺序固定为 NewsAPI.ai / Event Registry → Marketaux → Finnhub → EIA Open Data →
SEC EDGAR。一次只执行一个经用户明确授权的平台。平台注册、plan/quota/retention 填写和
用户/ChatGPT Review 未完成时，只能运行默认 dry-run。

```text
用户完成平台注册并将凭证保存在本地
→ 单个平台 bounded smoke
→ 用户和 ChatGPT 审核 redacted report
→ 确认字段、额度、许可和保存边界
→ 编写或激活对应 SPEC
→ Codex 实现 Provider Adapter
→ 测试、审查和合并
```

## Local credentials

1. 复制 `.env.example` 为本地 `.env`，或从
   `docs/PROVIDER_CREDENTIALS_TEMPLATE.md` 复制为仓库根目录
   `PROVIDER_CREDENTIALS_PRIVATE.md`。
2. 只在本地 secret storage / `.env` 中填真实值。两个私有文件均被 Git ignore；运行前仍用
   `git status --short` 确认。
3. 不把 key 放入命令行、截图、日志、issue、PR 或聊天。
4. `.env` 不会被普通 shell 自动加载。可由用户在受控 shell 中导出；不要把包含 secret 的
   shell 命令粘贴到 Review。

## Dry-run

以下命令默认不会联网，也不要求凭证：

```bash
uv run python scripts/provider_smoke.py --provider newsapi_ai --query technology --max-results 1
uv run python scripts/provider_smoke.py --provider marketaux --query technology --limit 1
uv run python scripts/provider_smoke.py --provider finnhub --symbol AAPL
uv run python scripts/provider_smoke.py --provider eia --dataset electricity --limit 1
uv run python scripts/provider_smoke.py --provider sec_edgar --ticker AAPL
```

Dry-run 返回 `BLOCKED`、空 HTTP/schema 字段是预期行为，仅证明命令和 redaction gate 可用。

## Explicit bounded execution

只有用户逐平台授权后，才在已加载本地环境变量的 shell 中为对应命令添加 `--execute`：

```bash
uv run python scripts/provider_smoke.py --provider newsapi_ai --query technology --max-results 1 --execute
uv run python scripts/provider_smoke.py --provider marketaux --query technology --limit 1 --execute
uv run python scripts/provider_smoke.py --provider finnhub --symbol AAPL --execute
uv run python scripts/provider_smoke.py --provider eia --dataset electricity --limit 1 --execute
uv run python scripts/provider_smoke.py --provider sec_edgar --ticker AAPL --execute
```

不要批量或循环执行。每条命令只构建一个最小请求；超出允许的 result bound、缺凭证或 EIA
version / SEC ticker 不在 scaffold allowlist 时 fail closed。

## Report interpretation

输出是单行 redacted JSON：

- `PASS`：2xx、有效 JSON，且可提取结构；仍需合同和 Review gate。
- `BLOCKED`：缺凭证、401/402/403/429、5xx、timeout/connection failure；停止，不自动重试。
- `FAIL`：其他 HTTP 错误或成功响应不是有效 JSON；停止并审查合同或 endpoint。

报告只允许出现 endpoint、status、字段名、数量以及指定 header 是否存在。若出现 secret、
真实 title/body/URL 值或完整 payload，立即停止，不分享输出，并作为安全缺陷处理。

## PASS criteria

1. 获得预期 endpoint 的 2xx 和有效 JSON。
2. 顶层/item 字段结构符合 `PROVIDER_OFFICIAL_CONTRACTS.md`。
3. result bound 生效，报告未包含内容值或 secret。
4. plan、quota、rate limit、allowed retention、internal AI/redistribution 边界已另行核对。
5. 用户和 ChatGPT 审核 report 并明确给出下一步授权。

任何关键合同仍 Pending、认证/额度错误、429/timeout、schema 不足或安全输出违规，均不得开始
Adapter implementation。
