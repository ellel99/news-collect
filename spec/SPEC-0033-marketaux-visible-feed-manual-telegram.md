# SPEC-0033 — Marketaux Visible Feed + Manual Telegram Push

Status：Active — Implementation Review

Phase：Phase 1 — Visible Marketaux Metadata and Explicit Manual Push

Foundation：v2.1-FROZEN（unchanged）

Depends on：SPEC-0032（Completed）

## 1. 目标

在已批准的 manual Marketaux collection-to-evidence pipeline 后增加最小可见层：将 adapter 已
allowlist/sanitize 的 title、public article URL、provider item id 与 published_at 写入现有
`ContentItem`，提供 read-only recent feed，并支持默认 dry-run 的 Telegram preview 与显式 manual push。

本 SPEC 写 Python implementation code，不是 docs-only；不新增 table/field/index/enum，不修改 migration、
ORM 或 DB schema。

## 2. Visible feed contract

- Marketaux adapter 只把 `display_title` 与 `display_url` 加入同一运行期 sanitized sidecar；RawItem 仍
  只保存 metadata-only envelope，不保存 response/body/HTML。
- URL 只接受 `http`/`https` public URL；secret-bearing title/URL fail closed 为不可展示字段。
- Evidence projection 继续使用原 content-free allowlist，display 字段不会进入 evidence mapper contract。
- 成功 collection/evidence 后，以 raw/source/account provenance 幂等创建现有 `ContentItem`：
  `content_kind=article`、`body=NULL`、`body_availability=unavailable`、metadata retention 为
  `metadata_only`。
- recent feed 只读返回 title、source/provider、published/collected time、canonical URL、provider item id、
  raw item id 与可选 evidence item id。
- 旧的 content-free RawItem 不进行猜测性回填；只有本 SPEC 生效后的新采集可形成可见 ContentItem。

## 3. Manual commands

Read-only feed：

```bash
python3 scripts/marketaux_feed_smoke.py --limit 10
```

Telegram preview（默认；读 DB，但不读 Telegram credential、不请求 Telegram）：

```bash
python3 scripts/telegram_marketaux_push_smoke.py --limit 3
```

用户单独授权后的 manual push：

```bash
TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... \
  python3 scripts/telegram_marketaux_push_smoke.py --execute --limit 3
```

- token/chat id 只从 process environment 读取；Settings 强制 `_env_file=None`。
- push limit 默认 3、最大 5；一次命令只发送一条 bounded plain-text message，不循环、不调度。
- message 只包含标题、来源、时间、链接。
- output/log/repr 不得包含 token/chat id、raw response、body/snippet/description 或 Telegram response body。

## 4. Safety and failure behavior

- preview 不读取 credential、不请求 Telegram；execute missing credential 在 DB/transport 前 fail closed。
- tests/CI/package review 只使用 mock provider/Telegram transport，不请求真实 Marketaux/Telegram。
- feed/Telegram transport failure 只返回固定 safe code，不回显 request URL、payload 或 secret。
- 不保存 Telegram response，不提交 live output。
- ContentItem persistence 出错时 pipeline 返回 safe invalid outcome，不静默声称可见 feed 成功。

## 5. 非范围

- 无 scheduler、自动推送、Notification/Outbox workflow。
- 无 AI、投资建议、formal normalization、dedup、clustering 或 Event。
- 无 Finnhub/EIA/SEC 或其他 Provider。
- 不抓 article page，不保存 full body/raw response。
- 不读 `.env`、`local_evaluation/` 或 raw capture；不执行 `provider_capture.py --execute`。
- 不启动 SPEC-0022；不修改 Foundation。

## 6. 测试与验收

- [x] mocked Marketaux collection 生成 ContentItem 与 recent visible feed。
- [x] feed 包含 title/source/time/link 与 raw/evidence provenance；body/source summary 不保存。
- [x] recent list bounded/ordered。
- [x] Telegram preview 不读 token、不请求 API。
- [x] missing credential、limit >5 fail closed。
- [x] mocked Telegram execute 格式正确，credential/response body 不进入 output/repr。
- [x] source audit 无 scheduler/OpenAI/recommendation/dedup/Event/local capture dependency。
- [x] 无 migration/ORM/schema 变化；SPEC-0022 未启动。
- [ ] Reviewer、CI、完整 regression 与 review package PASS。

## 7. Verification Evidence

以本 PR diff、mock-only provider/Telegram tests、PostgreSQL/Redis integration tests、full regression、
Foundation validation 与 committed-snapshot review package 为证。本 PR/CI 不请求真实 Provider 或 Telegram。

## 8. Review History

| Round | Result | Evidence | Resolution |
|---|---|---|---|
| 1 | IN REVIEW | 本 implementation PR、CI 与 review package | 等待用户/ChatGPT Implementation Review |
