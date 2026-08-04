# SPEC-0030 — Marketaux Real Adapter Implementation

Status：Active — Implementation Review

Phase：Phase 1 — Marketaux Real Adapter Code Boundary

Foundation：v2.1-FROZEN（unchanged）

Depends on：SPEC-0025、SPEC-0026、SPEC-0029（Completed）

## 1. 目标

实现 Marketaux production request code boundary：显式 runtime credential、allowlisted request、真实
HTTP transport interface/implementation、timeout、HTTP/rate-limit classification、response validation、
content-free sanitized metadata、deterministic cursor 与 metadata-only `RawItemEnvelope`。

本 PR 写 Python implementation code，不是 docs-only。测试全部使用 mocked transport；本 PR 不执行
任何真实 Marketaux 或其他 Provider 请求。

## 2. Credential boundary

- credential 必须由调用方以 `RuntimeCredential` constructor injection 提供；代码不读 `.env`、OS
  environment、CLI 或 config secret。
- provider-neutral `ProviderTransportRequest.params` 严禁 secret；credential 使用 repr-hidden runtime
  field 单独传到最终 transport。
- Marketaux 官方合同要求 wire-level `api_token` query parameter。`HttpxProviderTransport` 只在发送
  瞬间把 opaque credential 注入该 query；不得把完整 URL、token 或底层 HTTP exception request 写入
  result、error、cursor、RawItem、日志或 package。
- missing/wrong credential reference 必须在 transport call 前 fail closed。

## 3. HTTP transport contract

- 只允许 `marketaux/news_all` → `GET https://api.marketaux.com/v1/news/all`，unknown operation fail closed。
- non-secret params allowlist 由 adapter 生成：`search`、`limit`、`page`、可选 `language`、`symbols`、
  cursor 后的 `published_after`。
- `Accept: application/json`；每次 request 使用显式 timeout。
- response 只传 status、parsed JSON 与 safe header allowlist（Content-Type、Retry-After、rate/usage limit）。
- httpx timeout 转为 `ProviderTransportTimeout`；其他 transport failure 转为固定 safe error，均不得串联
  secret-bearing request details。

## 4. Adapter contract

- record limit 为 1–3；超限、非法 config/cursor 在 transport 前拒绝。
- HTTP 429 读取 bounded `Retry-After` 并产生 retryable rate-limit error。
- 5xx 为 retryable upstream error；其他非 2xx 为 non-retryable safe upstream error。
- 2xx 必须有 `data` list；item 必须有安全 `uuid` 与 `published_at`，否则 fail closed。
- stable identity 优先 `uuid`；cursor 为确定性的 `published_at + provider_item_id`。
- output 只含 content-free field names/presence flags、hash、opaque internal reference；不含真实 title、
  body、URL、snippet、description 或 secret。
- RawItemEnvelope 固定 `metadata_only`；不访问 article URL、不下载全文。

## 5. 严格非范围

- 不请求真实 API；CI/default command 也不得请求。
- 不读取 `.env`，不执行 `provider_capture.py --execute`，不读取/提交 capture/`local_evaluation/`。
- 不实现 Finnhub/EIA/SEC 或其他 real adapter。
- 不实现 scheduler、formal normalization、dedup、clustering、Event 或 AI。
- 不实现 Telegram、investment recommendation、Portfolio/Holding 或交易动作。
- 不修改 migration、ORM、table、column、index、constraint 或 DB schema。
- 不启动 SPEC-0022；SPEC-0005 X Source 范围不变。

## 6. 测试要求

- mocked HTTP transport 验证 endpoint/method/non-secret params 与最终官方 query injection；
- credential 不进入 params/result/error/cursor/RawItemEnvelope/repr；missing credential fail closed；
- mocked 200 → metadata-only RawItemEnvelope + sanitized metadata；provider echo secret 不进入 output；
- mocked 429/Retry-After、timeout、4xx、5xx、malformed body 与 limit failure；
- cursor deterministic、无 secret；
- source audit 禁止 env/local capture/scheduler/DB/EvidenceWriteService/Event/AI/Telegram dependency；
- existing scaffold、collection 与 end-to-end mock regressions 保持通过；
- 测试不得访问真实网络。

## 7. 验收标准

- [x] RuntimeCredential 与 HTTP transport boundary 已实现。
- [x] Marketaux real adapter boundary 已实现。
- [x] 官方 endpoint/method/query-token wire contract 由 mock HTTP test 覆盖。
- [x] 200/429/timeout/4xx/5xx/malformed/limit/cursor tests 已覆盖。
- [x] secret 不进入 provider-neutral params、result/error/cursor/RawItemEnvelope。
- [x] 无真实 API、`.env`/capture、Finnhub/EIA/SEC、scheduler、dedup/Event/AI/Telegram。
- [x] 无 migration/ORM/DB schema change；SPEC-0022 未启动。
- [ ] Reviewer/CI/完整验证与安全 review package PASS。

## 8. Verification Evidence

以本 PR source diff、httpx MockTransport、network-free provider mock tests、full regression、source audit、
Foundation validator 与安全 review package 为证。本 PR 未执行真实 API request。

## 9. Review History

| Round | Result | Evidence | Resolution |
|---|---|---|---|
| 1 | IN REVIEW | 本 implementation PR、CI 与 review package | 等待用户/ChatGPT Implementation Review |

## 10. 后续门禁

本 SPEC PASS 只批准 Marketaux real adapter code boundary，不等于授权真实执行。credential deployment
wiring、真实 collection enablement、scheduler、其他 Provider、dedup/Event/AI/Telegram 仍需独立授权。
