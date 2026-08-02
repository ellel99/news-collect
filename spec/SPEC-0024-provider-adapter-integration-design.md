# SPEC-0024 — Provider Adapter Integration Design

Status：Completed — Docs Review approved

Phase：Phase 1 — Provider Adapter Integration Design Only

Foundation：v2.1-FROZEN（unchanged）

Depends on：SPEC-0003、SPEC-0020、SPEC-0023 implementation（Completed）

## 1. 目标

设计未来如何把四个已确认 Provider 的真实 polling Adapter 安全接入现有 collection runner，并在
后续独立 pipeline stage 中生成 `CommonEvidenceEnvelope`、调用 Evidence Write Path。本文只定义
边界、合同、失败与测试门禁，不实现 Python Adapter、注册、collection integration 或外部请求。

Provider 范围固定为：

- Marketaux；
- Finnhub；
- EIA Open Data；
- SEC EDGAR。

不得新增或重新选择 Provider。NewsAPI.ai / Event Registry 保持 future/blocked；GDELT 保持
runtime blocked/future evaluation only，二者都不是本 SPEC 的实现对象。

## 2. 架构边界

未来主链路必须分层：

```text
Source + optional SourceAccount + cursor + provider config
→ Provider Adapter（fetch + sanitize）
→ FetchBatch / RawItemEnvelope
→ CollectionRunner（RawItem persistence + atomic cursor checkpoint）
→ 独立 mapping stage（provider_mappings.py）
→ CommonEvidenceEnvelope
→ 独立 Evidence Write Path stage（EvidenceWriteService）
→ evidence_items
```

Adapter 只负责 fetch、最小响应验证、安全清洗和 collection contract 输出。Adapter：

- 不直接写 `evidence_items`；
- 不导入或调用 `EvidenceWriteService`；
- 不执行 evidence mapping；
- 不写 RawItem、ContentItem 或其他 DB row；
- 不推进 cursor；cursor 只由 collection runner 在 RawItem 安全持久化后 checkpoint。

## 3. Adapter 输入合同

每个未来 Adapter 的 `fetch` 输入必须由 collection framework 显式提供：

- 已通过 authorization gate 的 `Source` identity/config projection；
- 可选且已验证归属/enabled 的 `SourceAccount` projection；
- immutable cursor/checkpoint snapshot；
- provider config（endpoint family、非 secret selectors、limits、timeout）；
- rate-limit policy 与本次 collection deadline；
- page/record limit 和 cancellation/remaining-time context。

Credential 只能通过运行时 secret reference 解析，不进入 request dataclass、cursor、task args、日志或
数据库。Adapter 不自行读取 Source 表或改变 authorization 状态。

## 4. Adapter 输出合同

输出复用现有 `FetchBatch` / `RawItemEnvelope` 或保持同等语义的 collection contract：

- stable external ID（可用时）；
- fetched/source published timestamps；
- HTTP status/content type 的安全 metadata；
- payload hash 与许可允许的最小 payload location/reference；
- retention/access/license classification；
- next cursor/watermark 与 `has_more`；
- sanitized provider metadata；
- safe classified collection error（失败时）。

输出不得包含 API key/token、Authorization header、secret-bearing URL、完整 request object、未授权
正文或未经清洗的 provider echo。Adapter 输出不是 `CommonEvidenceEnvelope`，也不直接触发 evidence
write。

## 5. Provider-specific 设计

### 5.1 Marketaux

- 角色：news signal metadata/public summary polling，不是付费媒体全文替代。
- 只保留合同允许的 title/metadata/public summary/source link presence 与最小 raw reference；不得把
  Bloomberg、WSJ、Reuters 或其他来源全文作为成功标准。
- credential 使用 header/query 的最终方式必须按合同 Review 固定；任何 token-bearing URL 不得进入
  RawItem、error 或日志。
- stable identity 优先 provider UUID；cursor 使用 published time watermark + deterministic tie-breaker，
  并保留重叠窗口以抵抗边界遗漏。
- interval、quota、429、timeout 与 retention 必须由用户 plan/合同确认后才能进入实现 SPEC。
- 不自动分页；page/record 上限由单次 collection target 固定。

### 5.2 Finnhub

- 角色：最小 quote metadata/evidence input；不实现 Market Validation 或投资建议。
- credential 优先 `X-Finnhub-Token` runtime header；header value 不落库、不记录。
- symbol scope 来自授权 SourceAccount/collection config allowlist，不允许 Adapter 扩大 symbol 集合。
- timestamp `t` 与 symbol-scoped identity 构成水位/幂等候选；没有有效 timestamp 时 fail closed，不猜测。
- RawItem 只保存合同允许的 quote response 最小结构/reference；quote value 不扩散到日志、error、cursor
  或禁止字段，后续 mapping 只生成 numeric presence/count。
- timeout、quota、429/Retry-After 和最小间隔按 plan 确认。

### 5.3 EIA Open Data

- 角色：official energy evidence，范围固定为获批 dataset/route（当前候选 electricity）。
- API key 只能在 runtime request 中使用；EIA `request` echo、query metadata 和 URL 写入 RawItem 前必须
  递归 sanitize，失败则整项 fail closed。
- identity/cursor 候选为 period + geography + sector；period 单调水位、明确 sort 与有限 overlap。
- geography/sector selectors 必须来自配置 allowlist；nullable numeric fields 合法，不得填充或猜测。
- RawItem 可保存许可允许的最小官方数据结构或安全 reference；不得在日志/error/cursor 扩散数值。
- numeric quota、timeout、429/retry 与 retention 仍须实现前合同确认。

### 5.4 SEC EDGAR

- 角色：official disclosure metadata，只使用 submissions/recent filings 范围。
- 必须使用合规且本地配置的 User-Agent/contact；不得把 contact、完整 header 或 request URL 入库。
- 不下载 filing body、`primaryDocument` 内容、附件或历史 bulk archives。
- RawItem 只保存许可允许的 submissions/recent filing metadata 或安全 reference；recent column arrays 必须
  有界。
- cursor 使用 accession identity + acceptance/filing timestamp；同 timestamp 使用 accession
  deterministic tie-breaker，不以下载顺序作为水位。
- 实现前必须固定 SEC fair-access 间隔、timeout、429/403/5xx handling 与 collection deadline。

## 6. Secret handling

- API key/token 只来自环境变量或未来批准的 secret manager，不允许 CLI 参数传入。
- task args、Source/SourceAccount config、cursor、RawItem、logs、errors、metrics 和 review package 不得包含
  secret value。
- 不保存带 credential 的完整 URL、Authorization 或 provider-specific secret header。
- Provider response 若回显 `api_key`、`api_token`、`token`、`authorization`、
  `x-finnhub-token` 或 secret-bearing URL，必须在形成 RawItemEnvelope 前递归移除/替换。
- sanitizer 无法证明输出安全时，Adapter 返回固定 safe error，不产生 RawItemEnvelope。
- error 只能使用 allowlisted code/message，不拼接 response、request、header 或 exception raw text。

## 7. Rate limit、timeout 与 retry

每个 Provider 的未来 implementation SPEC 必须固定数值策略，本文只批准共同算法边界：

- Provider/plan-specific 最小请求间隔，由共享 rate limiter 在请求前执行；
- HTTP 429 优先解析合法 `Retry-After`，否则使用 capped exponential backoff + full jitter；
- 408/429/明确 transient 5xx/timeout 可 bounded retry；401/403、合同/shape/secret failure 不盲重试；
- retry 次数、单次 connect/read timeout、最大 backoff 与总 collection deadline 必须有有限上限；
- 任何 retry 不得扩大 query、symbol、dataset、ticker、page 或 time window；
- deadline 剩余不足时停止并返回 classified error，不进入无限 loop；
- 错误映射到 SPEC-0003 allowlist（authentication、rate-limit、timeout、provider/contract、unexpected
  response 等），不回显 raw details。

数值 interval/quota 尚未由 plan/terms 确认时保持 implementation blocker，不得猜测默认值。

| Provider | Minimum interval design gate | Timeout / run deadline gate |
|---|---|---|
| Marketaux | 必须从用户 plan、quota 与实际 rate headers 推导并配置正数；未确认时 blocked | connect/read timeout、attempt cap 与 total deadline 必须在实现 SPEC 固定 |
| Finnhub | 必须从用户 plan rate limit 推导 per-key limiter；未确认时 blocked | quote request timeout 必须小于 target deadline，retry 后仍不得越过 deadline |
| EIA | 官方 numeric quota 未确认；必须配置经合同核验的正数间隔，未确认时 blocked | timeout/retry 上限必须在实现前验证，禁止用大窗口掩盖 timeout |
| SEC EDGAR | 全局间隔不得短于 100ms（遵守官方总计不超过 10 requests/second），实现可选择更保守值 | bounded submissions request，deadline 内不下载 filing/attachment |

所有 limiter 必须按 Provider/credential identity 共享，不能由并发 worker 各自计算而突破全局限制。
collection target 必须同时携带单次 request timeout 与 total run deadline；后者包括 rate wait、所有
attempt/backoff 与 response validation。

## 8. Cursor / checkpoint

Cursor 必须是 versioned、provider-scoped、deterministic JSON-compatible structure，至少包含：

- provider/contract version；
- last-seen provider timestamp/period；
- stable provider item ID 或 deterministic tie-breaker；
- page/cursor token（仅 Provider 明确支持且不含 secret 时）；
- query/symbol/dataset/ticker scope hash；
- optional overlap/window metadata。

Adapter 只提出 `next_cursor`；collection runner 在对应 RawItem batch 全部安全持久化后，与 RawItem 同一
transaction checkpoint。失败、partial 未达安全边界或 sanitizer rejection 不推进 cursor。

RawItem idempotency 继续使用 Source-scoped stable external ID/payload hash；cursor 不能替代唯一性。
Historical backfill 本 SPEC 只保留显式入口/独立 cursor namespace，不实现回补、自动分页或大规模抓取。

## 9. RawItem safety 与 retention

### 9.1 允许保存

- provider、contract/version、stable opaque identity；
- fetched/published timestamps、HTTP status/content type；
- sanitized field subset 或 opaque internal payload reference；
- payload hash、record count、safe response/header presence metadata；
- access level、license policy、retention class 和 parser contract version。

### 9.2 禁止保存

- secret、secret-bearing URL/header/request；
- 未授权 article/fulltext、网页 HTML、SEC filing/primaryDocument body；
- 未经批准的完整 response 或超出 record/page/window 限制的数据；
- Provider SDK object、connection/session metadata 或 raw exception。

### 9.3 Retention classes

- `metadata_only`：identity、timestamps、field/presence metadata 和安全 reference；
- `link_only`：只保留公开 source link 的安全引用与 metadata，不下载正文；
- `redacted_summary`：仅在合同明确允许保存 public summary 且已执行 provider-specific redaction 时使用；
- 任何 full/raw retention 必须由未来 Provider implementation SPEC、license review 和用户明确批准。

Adapter 把 access/license/retention 分类传递给 RawItemEnvelope；collection runner 不得擅自扩大。

## 10. Mapping handoff

RawItem persistence 成功后，未来独立 pipeline stage 才能：

1. 读取被许可的 sanitized RawItem projection（不是 raw Provider SDK response）；
2. 调用对应 `provider_mappings.py` pure mapper；
3. 验证 `CommonEvidenceEnvelope`；
4. 显式构造 provenance context；
5. 调用 `EvidenceWriteService`。

Adapter 不导入 mapper 或 Write Path。Mapping/Write failure 使用固定 safe processing/collection error，
不回滚已经合法保存的 RawItem、不推进未安全完成的后续 checkpoint，也不静默丢弃。该 pipeline stage
的 runtime orchestration 不在本 docs-only SPEC 中实现。

## 11. Collection runner integration boundary

未来实现需独立审核以下最小变化：

- 为四个明确 access method 注册真实 Adapter；fake Adapter 永久保留用于无网络测试。
- `AdapterRegistry` 未注册/未知 key 继续 fail closed，不 fallback 到网页抓取或其他 Provider。
- dispatcher/worker 继续要求 enabled Source，并只允许 `authorized` / `implemented`；planned、
  access_tbd、degraded、blocked、disabled 不调度。
- account target 必须存在、归属 Source 且 enabled；存在 account 时禁止隐式 source-level target。
- runner 继续拥有 lock、CollectionRun lifecycle、RawItem persistence 与 cursor transaction；Adapter
  不拥有这些职责。
- scheduler frequency 只能来自已批准 Source policy，不由 Adapter 自行安排或递归 enqueue。

是否扩展 `AdapterRegistry`、如何分 Provider 拆 implementation SPEC、以及 runtime wiring 必须在后续
Implementation Review 中逐项批准；本文不修改 registry/runner/task。

## 12. Future implementation tests

后续实现至少需要：

- unit tests 全部使用 mocked transport，不访问网络；
- 四 Provider request/response contract fixtures 为 synthetic、content-safe、无 secret；
- credential missing/unknown adapter/unauthorized source fail closed；
- logs、errors、RawItem、cursor、task args 和 package 无 secret-bearing URL/header/value；
- provider response echo secret 的 sanitizer 与 fail-closed tests；
- 429、Retry-After、timeout、transient 5xx、bounded retry、deadline exhaustion；
- cursor format/version、monotonicity、overlap、restart recovery 与 checkpoint atomicity；
- stable ID/payload hash idempotency、replay 和 duplicate fetch；
- per-provider record/page/scope limits 与 no automatic backfill；
- RawItem-only Adapter boundary：Adapter 不直接写 Evidence/Content/Notification/Outbox；
- mapping handoff mock：persisted sanitized projection 可交给 pure mapper；
- source audit：Adapter 不导入 `EvidenceWriteService`、Event、AI、Telegram 或 investment modules；
- existing fake Adapter、authorization/account gate、lock/retry/stale recovery regression。

PostgreSQL/Redis/Celery integration tests 只能验证本地基础设施；真实 Provider smoke 必须逐次获得用户
单独授权，且不由 test suite 自动执行。

## 13. 明确非目标

本 SPEC 不实现：

- Python Provider Adapter code 或 AdapterRegistry；
- collection runner integration、scheduler、task 或配置 wiring；
- external API request 或 `provider_capture.py --execute`；
- raw capture replay、读取/提交 `local_evaluation/`；
- migration、ORM、DB schema 或新表；
- formal normalization、canonicalization、dedup、clustering 或 Event；
- AI、Market Validation、Telegram、investment recommendation 或交易动作；
- SPEC-0022 或 SPEC-0005 X Source implementation。

## 14. Docs Review 验收标准

- [ ] 四 Provider 范围与职责经 Reviewer 批准，没有新增/重选 Provider。
- [ ] Adapter input/output、secret、rate/retry/timeout、cursor 与 RawItem safety 合同经批准。
- [ ] Mapping/Write Path handoff 与 Adapter 不直接写 Evidence 的边界经批准。
- [ ] Collection runner/registry authorization fail-closed 设计经批准。
- [ ] Future tests 和 strict non-scope 经批准。
- [ ] 明确 docs-only，无 Python、API、capture、migration/ORM/schema 或 runtime integration。
- [ ] SPEC-0022 未激活，SPEC-0005 范围不变，Foundation v2.1-FROZEN 未修改。

## 15. Verification Evidence

以本 PR Markdown-only diff、Foundation validator、现有回归测试和安全 review package 为证。没有
请求 API、执行 capture、读取 raw capture/`local_evaluation/` 或实现 Adapter/collection integration。

## 16. Review History

| Round | Result | Evidence | Resolution |
|---|---|---|---|
| 1 | IN REVIEW | 本 docs-only PR、CI 与 review package | 等待用户/ChatGPT Docs Review |
| 2 | PASS | PR #24、CI、209 tests 与安全 review package | Docs Review approved and merged |

## 17. 后续门禁

Docs Review PASS 只批准设计。用户随后单独授权 SPEC-0025 scaffold implementation；该授权不允许
真实 API、真实 Provider implementation、collection runner wiring，或开始 SPEC-0022、
dedup/Event/AI。

## 18. Completed Design Evidence

PR #24 已完成并合并 Provider Adapter Integration Design，覆盖四 Provider 的 input/output、secret、
rate/retry/timeout、cursor、RawItem safety、mapping handoff、registry/runner boundary 与 future tests。
PR #24 是 docs-only：没有 Python、Adapter、AdapterRegistry、collection integration、API request、
migration/ORM/schema 或 capture，也没有启动 SPEC-0022。
