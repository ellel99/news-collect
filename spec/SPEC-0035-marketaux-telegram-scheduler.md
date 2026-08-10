# SPEC-0035 — Minimal Scheduler for Marketaux + Telegram

Status：Completed — Implementation Review approved

Phase：Phase 1 — Minimal Automated Collection and Push

Foundation：v2.1-FROZEN（unchanged）

Depends on：SPEC-0033、SPEC-0034（Completed）

## 1. 目标

将已验证的 Marketaux collection → RawItem → evidence_items → metadata-only ContentItem → Telegram
链路封装成一个最小 Celery Beat cycle，并使用既有 Notification 唯一 dedup key 防止同一 ContentItem
重复自动推送。

## 2. 范围

- Celery task `marketaux.telegram.run` 与固定间隔 Beat entry。
- 默认 `execute=false`：不读 Provider/Telegram credential、不访问 DB/Redis、不请求网络。
- 只有 manual `--execute` 或 worker process 明确设置 execute runtime flag 时才读取 process environment。
- 每个 execute cycle limit 1–3；只收集 Marketaux。投递阶段同时处理本轮新 visible items，以及与本轮
  collection run 无关的可重试 FAILED / stale SENDING notifications。
- 复用既有 `notifications` 表：`dedup_key=marketaux:telegram:{content_item_id}`，unique constraint
  原子 claim，成功标记 sent，失败保留 failed safe state。
- `MAX_DELIVERY_ATTEMPTS=3`；FAILED 在 `retry_count < 3` 时可通过条件 UPDATE 原子回到 SENDING，
  并原子增加 retry_count。达到上限后保留 FAILED 审计记录且不再自动发送。
- `SENDING_STALE_AFTER_SECONDS=300`；未超时 SENDING 不可抢占，超时后可通过相同条件 UPDATE
  原子 reclaim，并计入 retry_count。
- manual smoke command 输出 content-free safe summary。

## 3. 安全和凭证

- `MARKETAUX_API_TOKEN`、`TELEGRAM_BOT_TOKEN`、`TELEGRAM_CHAT_ID` 只从 process environment 读取。
- Settings 均显式 `_env_file=None`；默认 dry-run 在 credential lookup 前返回。
- summary/log 不包含 token/chat id、完整 URL、raw response、title/body/snippet/description。
- 不保存 Telegram response body，不提交 live output。
- tests/CI 只使用 mocked Provider/Telegram transports。

## 4. 幂等与失败语义

- Notification unique dedup key 是最小 sent marker；claim 成功后才允许调用 Telegram。
- SENT marker 永久阻止同一 dedup key 再次发送；不得删除 Notification 绕过 dedup。
- Telegram 非 2xx/transport failure 将 claim 标记 FAILED 并保留 `failure_code`；下一周期可进行 bounded
  retry。失败达到上限后不再自动发送。
- FAILED retry 与 stale SENDING recovery 独立查询既有 Notification，不依赖 current collection run；
  provider 本轮无新 item 或失败时，已有 retryable notification 仍可投递。
- 原子 reclaim 使用 PostgreSQL 条件 UPDATE / RETURNING；并发 worker 只有一个能从 FAILED 或 stale
  SENDING 转为新的 SENDING attempt。
- 空 run feed 或全部已 claim 时返回 `NO_NEW_ITEMS`，不是错误。
- collection failure 不创建 Notification，也不请求 Telegram。

## 5. 非范围

- 无 AI、投资建议、formal dedup、clustering、Event 或 SPEC-0022。
- 无 Finnhub/EIA/SEC、X source 或其他 Provider。
- 无复杂规则引擎、独立 retry worker 或 Outbox publisher；retry 仅为本 scheduler 的既有 DB 状态机。
- 不修改 migration/ORM/DB schema，不绕过 migrate，不删除 volume。
- 不读取 `.env`、local capture；不保存 raw response。

## 6. 配置与命令

- `MARKETAUX_TELEGRAM_SCHEDULER_EXECUTE=false`
- `MARKETAUX_TELEGRAM_SCHEDULER_INTERVAL_SECONDS=900`
- `MARKETAUX_TELEGRAM_SCHEDULER_LIMIT=1`

默认 dry-run：

```bash
python3 scripts/marketaux_telegram_scheduler_smoke.py
```

用户明确授权的一次 manual execute：

```bash
MARKETAUX_API_TOKEN=... TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... \
  python3 scripts/marketaux_telegram_scheduler_smoke.py --execute --limit 1
```

长期 worker execute 必须由部署环境显式设置上述 execute flag 和 process credentials；默认配置不会发送。

## 7. 测试与验收

- [x] default dry-run 不读 credential、不访问 runtime、不发送。
- [x] execute missing credential fail closed。
- [x] mocked Marketaux → RawItem → evidence → ContentItem → Telegram PASS。
- [x] 同一 ContentItem 只创建一个 Notification，重复 cycle 不再次发送。
- [x] Telegram failure 保留 failed Notification 与 safe failure code。
- [x] FAILED → bounded retry → SENT，retry_count 原子递增；达到上限停止。
- [x] stale SENDING 可恢复，非 stale SENDING 不可重复 claim。
- [x] 并发 reclaim 只有一个 winner；SENT 永不重复发送。
- [x] retry 不依赖 current collection run；NO_NEW_ITEMS/provider failure 不阻止历史 retry。
- [x] Provider failure 不创建 Notification、不请求 Telegram。
- [x] safe summary 不含内容、URL 或 secret。
- [x] Celery task/Beat entry 已注册，默认 execute false。
- [x] 无 migration/ORM/schema change，无 AI/dedup/Event/多 Provider。
- [ ] CI、Docker mock smoke、Reviewer 与 review package PASS。

## 8. Review History

| Round | Result | Evidence | Resolution |
|---|---|---|---|
| 1 | IN REVIEW | implementation diff、mock tests、PostgreSQL/Redis integration、review package | 等待用户/ChatGPT Review |
| 2 | PASS | PR #34，CI PASS，可靠投递 review fixes | Approved and merged；SPEC-0035 Completed |
