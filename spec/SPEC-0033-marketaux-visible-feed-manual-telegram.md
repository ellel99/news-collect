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

- Marketaux adapter 将 content-free evidence metadata 与 allowlisted display projection 作为两个
  独立 contract 交给 same-run sidecar；`display_title` / `display_url` 不进入
  `RawItemEnvelope` 或 evidence metadata。RawItem 仍只保存 metadata-only envelope，不保存
  response/body/HTML。
- URL 只接受 `http`/`https` public URL；secret-bearing title/URL fail closed 为不可展示字段。
- Display projection 只允许 provider item id / published time / bounded non-secret title / public URL；
  `None`、过长 title、credential-bearing URL 或 token-like value 均不传递。
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

验收时必须要求至少一条可见项：

```bash
python3 scripts/marketaux_feed_smoke.py --limit 3 --require-items
```

`--require-items` 在空 feed 时返回 `BLOCKED` / `visible_feed_empty`；默认 read-only
查询仍允许空结果为 PASS。

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

- preview 不读取 credential、不请求 Telegram。execute 也必须先读 feed；feed empty
  时不读 token/chat id、不请求 Telegram，只有 feed 非空才读 credential。
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
- [x] Marketaux cursor 在下次 provider request 前转换为合同允许的 UTC datetime
  参数，不将 cursor 封装格式直接传给 provider。
- [x] RawItem/evidence projection 与 visible display projection 严格分离。
- [x] feed 包含 title/source/time/link 与 raw/evidence provenance；body/source summary 不保存。
- [x] recent list bounded/ordered。
- [x] Telegram preview 不读 token、不请求 API。
- [x] feed empty 时 Telegram execute 不读 credential、不发送请求。
- [x] `--require-items` 在空 feed 时 fail closed。
- [x] collection failure summary 仅返回 collection run 存在性/status/error code 及固定
  allowlisted safe detail，不回显 provider content/request。
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
| 2 | REQUEST CHANGES | 用户本地首次 real collection 为 `COLLECTION_CONTRACT_INVALID` / `provider_request_rejected`，本次 fetched/new/duplicate 均为 0；feed empty，Telegram blocked | 分离 display projection，正规化 cursor request datetime，细化 safe diagnostics，新增 feed `--require-items`，并将 Telegram credential 读取后移到 non-empty feed gate 之后；尚未声称 post-fix live PASS |

## 9. Known environment issue (separate follow-up)

用户本地 `docker compose up -d api` 触发 migrate 时曾报 `Can't locate revision identified
by '0003'`。该环境/Alembic 状态不通过删除 volume 掩盖，本 PR 不修改 migration、ORM
或 DB schema；待独立审核任务诊断。
