# Provider Smoke Runbook

## Gate

当前执行顺序固定为 Marketaux → Finnhub → EIA Open Data → SEC EDGAR。一次只执行一个经
用户明确授权的平台；前一个 smoke 完成后必须停止并等待用户/ChatGPT Review。NewsAPI.ai /
Event Registry 为 `future / blocked`，不在当前执行序列，CLI 对其 `--execute` fail closed。
平台注册、runtime credential 配置和用户逐平台授权未完成时，只能运行默认 dry-run。
plan / quota / retention / internal AI allowed 属于 optional contract review metadata：缺失不阻塞
最小 bounded smoke，但会阻塞后续合同 PASS、Adapter implementation 和正式采集。

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

1. 在仓库根目录复制环境模板：

   ```bash
   cp .env.example .env
   ```

   填写本地 `.env`。CLI 默认尝试读取仓库根目录 `.env`；不存在时忽略。也可用
   `--env-file <path>` 指向另一个本地环境文件。已存在的 OS 环境变量优先于文件值。
2. 或从
   `docs/PROVIDER_CREDENTIALS_TEMPLATE.md` 复制为仓库根目录
   `PROVIDER_CREDENTIALS_PRIVATE.md`。
3. 只在本地 secret storage / `.env` 中填真实值。两个私有文件均被 Git ignore；运行前仍用
   `git status --short` 确认。
4. 不把 key 放入命令行、截图、日志、issue、PR 或聊天。

## Dry-run

以下命令默认不会联网，也不要求凭证：

```bash
uv run python scripts/provider_smoke.py --provider marketaux --query technology --limit 1
uv run python scripts/provider_smoke.py --provider finnhub --symbol AAPL
uv run python scripts/provider_smoke.py --provider eia --dataset electricity --limit 1
uv run python scripts/provider_smoke.py --provider sec_edgar --ticker AAPL
```

Dry-run 返回 `BLOCKED`、空 HTTP/schema 字段是预期行为，仅证明命令和 redaction gate 可用。

## Explicit bounded execution

只有用户逐平台授权后，才在已加载本地环境变量的 shell 中为对应命令添加 `--execute`：

```bash
uv run python scripts/provider_smoke.py --provider marketaux --query technology --limit 1 --execute
uv run python scripts/provider_smoke.py --provider finnhub --symbol AAPL --execute
uv run python scripts/provider_smoke.py --provider eia --dataset electricity --limit 1 --execute
uv run python scripts/provider_smoke.py --provider sec_edgar --ticker AAPL --execute
```

如果已按上文填写仓库根目录 `.env`，无需手工 `export`。使用其他本地文件时，在命令末尾增加
`--env-file /path/to/private.env`；该参数只能传文件路径，不能传 key/token。

不要批量或循环执行。每条命令只构建一个最小请求；超出允许的 result bound、缺凭证或 EIA
version / SEC ticker 不在 scaffold allowlist 时 fail closed。

当前只允许在用户明确确认后单独执行 Marketaux。完成后必须停止；不得自动继续 Finnhub、
EIA 或 SEC。NewsAPI.ai 即使存在本地 key 也不得执行。

## Report interpretation

输出是单行 redacted JSON：

- `PASS`：2xx、有效 JSON、result count 大于零，并满足 provider-specific schema：
  list provider 必须有 item fields；Finnhub 必须出现 quote 候选字段；SEC 必须出现
  `filings.recent` columnar fields。仍需合同和 Review gate。
- `BLOCKED`：缺凭证、401/402/403/429、5xx、timeout/connection failure；停止，不自动重试。
- `FAIL`：其他 HTTP 错误或成功响应不是有效 JSON；停止并审查合同或 endpoint。

报告只允许出现 endpoint、status、字段名、数量以及指定 header 是否存在。若出现 secret、
真实 title/body/URL 值或完整 payload，立即停止，不分享输出，并作为安全缺陷处理。

## PASS criteria

1. 获得预期 endpoint 的 2xx 和有效 JSON。
2. 顶层/item path 和字段结构符合 `PROVIDER_OFFICIAL_CONTRACTS.md`；2xx + JSON 但 path
   缺失、列表为空或 item fields 为空时不得 PASS。
3. result bound 生效，报告未包含内容值或 secret。
4. 用户和 ChatGPT 审核 redacted structural report。

以上只定义 structural smoke PASS。进入合同 PASS、Adapter implementation 或正式采集前，还
必须另行核对 plan、quota、rate limit、allowed retention、internal AI/redistribution 边界，
且获得用户明确授权。

任何关键合同仍 Pending、认证/额度错误、429/timeout、schema 不足或安全输出违规，均不得开始
Adapter implementation。

## Marketaux bounded smoke evidence

- 执行状态：用户单独授权的一次 bounded smoke 已完成。
- 结果：HTTP 200、有效 JSON、`data` / `meta` 顶层结构、1 个结果、预期 article 字段结构和
  rate/usage-limit headers 均已观察到；classified result 为 `PASS`。
- 安全边界：只记录 redacted structural result；未记录 token、完整 response、真实
  title/body/URL 或 raw payload。
- 当前 gate：仅 structural smoke PASS；合同 PASS、Adapter implementation 和正式采集仍未
  授权。Finnhub、EIA、SEC 尚未执行。
