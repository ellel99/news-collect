# SPEC-0006 — Raw Capture & Replay Evaluation

状态：Active — Scaffold Review（real capture not started）
阶段：Phase 1 — Evaluation Tooling
负责人：Project Owner
创建日期：2026-08-01
最后更新：2026-08-01

## 1. 目标

提供一个 fail-closed、本地专用的 raw provider capture、audit 和 replay summary 脚手架，使少量
经批准的 API 响应可在不重复请求 Provider 的前提下用于后续 parser 和 pipeline 评估。

本轮只交付 SPEC、脚手架和 mock-only tests，不执行真实 capture。

## 2. 背景

Marketaux、Finnhub、EIA Open Data、SEC EDGAR 已分别完成一次用户授权的 bounded structural
smoke。Smoke 只证明最小响应结构可见，不提供可重复的 parser fixture。真实 raw response
不得进入 Git、PR、issue 或 chat，因此需要本地 capture、脱敏 audit report 和 replay summary
三层边界。

## 3. Foundation 与阶段边界

- Foundation：v2.1-FROZEN；不修改冻结边界。
- 当前阶段允许：本地 evaluation tooling、原始留痕安全规则、确定性结构检查。
- 当前阶段禁止：AI API、Event/Evidence/Analysis、Portfolio、Market Validation implementation、
  投资建议和自动交易。
- SPEC-0005 保持 `Approved X Source and Account Collection` Planned 范围，不被本 SPEC 使用或
  改写。
- 原 Planned `Normalization, Deduplication and Outbox` 主题保留为未编号待排期项；本 SPEC
  不实现该 pipeline。

## 4. 前置条件与依赖

- 前置：PR #13 provider preflight scaffold 已合并；四个 Provider structural smoke PASS。
- 外部权限：未来真实 capture 必须逐次获得用户明确授权；本轮没有授权真实请求。
- 本地凭证：只允许 OS env 或 gitignored `.env` / `--env-file`；OS env 优先。
- 本地目录：`local_evaluation/` 及所有子目录必须 gitignored。
- 用户确认：真实 capture 的 Provider、selector、limit 和单次请求必须逐次确认。

## 5. 范围

- `scripts/provider_capture.py`：默认 dry-run；显式 `--execute` 才允许单次请求并写本地 capture。
- `scripts/provider_capture_audit.py`：只读 capture，输出 redacted audit JSON。
- `scripts/provider_replay.py`：只读单个 capture，输出 content-free replay summary。
- Provider：Marketaux、Finnhub、EIA Open Data、SEC EDGAR。
- Mock-only tests、安全门禁、limit 检查和 SEC columnar truncation。

## 6. 非范围

- NewsAPI.ai / Event Registry、GDELT、Reuters、Bloomberg、WSJ、CNBC、X Source。
- Adapter / adapter registration、collection runner、scheduler、自动循环、分页、backfill。
- 数据库、migration、schema、ORM、RawItem/ContentItem persistence。
- 下载 article page、HTML、SEC filing body 或 `primaryDocument`。
- normalization、dedup、clustering、entity mapping、importance、impact、scoring 或 Market
  Validation 的实际算法；这些只是未来 replay consumer 候选。
- AI API、投资建议或交易动作。

## 7. 功能需求

### FR-01 Local-only raw capture

- 默认 dry-run；只有 `--execute` 才可能请求一次。
- CLI 不接受 key/token；credential 只从 OS env / `.env` / `--env-file` 读取。
- 不自动循环、调度、分页或 follow article / filing links。
- 输出只能写入 `local_evaluation/raw_provider_captures/`。
- stdout 只输出 path/hash/size/status 等安全元数据，不输出 response body。

Raw capture 允许字段：

- `capture_version`、provider、`captured_at`、`endpoint_family`；
- request method、non-secret params、secret param/header names；
- query / symbol / dataset / ticker、limit；
- HTTP status、safe response header subset；
- 截断后的原始 JSON response structure/body。

Raw capture 禁止：

- key/token/`.env`、Authorization 或 `X-Finnhub-Token` value、含 secret 的 request URL；
- raw request object/header values containing secrets；
- article HTML、SEC filing/`primaryDocument` body；
- 自动分页、backfill 或批量结果。

### FR-02 Provider bounds

| Provider | 单次边界 | 写盘前约束 |
|---|---|---|
| Marketaux | 单 query；`limit <= 3`；单 run 总 article `<= 15` | `data` 截断到 requested limit；不访问 article URL |
| Finnhub | 单 symbol；scaffold allowlist 最多 5 个 symbol 候选 | 只保存 quote JSON；不实现 Market Validation |
| EIA | `dataset=electricity`；`limit <= 5` | `response.data` 截断；无 pagination/backfill |
| SEC EDGAR | 单 ticker；scaffold ticker universe `<= 3`；recent filings `<= 10` | `filings.recent` 每个 column array 截断到 limit；不下载 filing |

建议的未来 Marketaux selector：`artificial intelligence`、`Nvidia`、`semiconductor`、
`data center energy`、`crypto`。每次仍须单独授权，不得由脚本自动遍历。

### FR-03 Audit report

`provider_capture_audit.py` 只能读取 `local_evaluation/raw_provider_captures/*.json`，不得导入
网络 client 或请求外部 API。每个 report 至少输出：

- provider、capture file path、file size bytes、SHA-256、captured_at、HTTP status；
- top-level/item field names、result count、safe header names；
- query/symbol/dataset/ticker、limit；
- `has_secret_detected`、`has_raw_request_url_with_secret`、
  `has_authorization_header`、`within_limit`、`replay_ready`、errors。

Report 禁止输出 title/body/URL/quote/EIA/filing value、raw response、secret 或 `.env`。
Capture 目录为空时不是 PASS：必须输出 `no_captures_found`，并以 exit code `2` fail closed。

### FR-04 Replay summary

`provider_replay.py` 只能读取一个 local capture，不请求网络、不调用 AI、不写数据库。初期只
输出：provider、input items、`normalized_items=0`、missing required fields、dedup-key
availability count、entity/timestamp field availability、`replay_ready` 和 errors。

`replay_ready=true` 只表示 capture 含有至少一个可作为后续 replay 输入的 item，且 summary
未发现错误；它不表示 normalization、dedup 或任何下游 pipeline 已实现。未知 Provider 或空
items 必须令 `replay_ready=false`，并输出 content-free error code。

未来可由独立 SPEC 扩展 normalization、dedup、entity mapping、clustering、importance、impact、
Market Validation 或 scoring；本 SPEC 不实现也不宣称这些能力。

### FR-05 ChatGPT review boundary

允许用户提供：audit report、replay summary、少量人工脱敏 preview。

禁止提供：API key/token、`.env`、full raw response、带 token 的完整 URL、Authorization header、
complete payload、真实未脱敏内容。

## 8. 非功能需求

- 安全：secret fail closed；local directories gitignored；输出 content-free。
- 可靠性：JSON 无效、路径越界、limit 越界或 secret risk 均不得标记 replay-ready。
- 可重复性：capture 包含版本、时间、hash 和确定性结构元数据。
- 合规：不绕过权限、不下载正文、不扩大 Provider 调用。
- 性能：仅处理受限本地 JSON；不支持大规模数据集。

## 9. 数据模型变化

无数据库、schema、ORM、表、字段、索引或外键变化。本地 JSON 不是产品数据模型或正式存储。

## 10. 接口与任务变化

- CLI：`provider_capture.py`、`provider_capture_audit.py`、`provider_replay.py`。
- 无 API、Celery task、scheduler、queue、Bot 或 collection task 变化。

## 11. 配置变化

不新增 credential；复用 preflight `.env`。新增 gitignored local paths：

```text
local_evaluation/
local_evaluation/raw_provider_captures/
local_evaluation/replay_outputs/
local_evaluation/audit_reports/
```

## 12. 错误处理

| Error | 条件 | Retry | 表现 |
|---|---|---|---|
| `CAPTURE_BLOCKED` | 缺 credential、无 `--execute` 或网络/JSON 失败 | 否 | 不写 capture；输出安全状态 |
| `CAPTURE_LIMIT_INVALID` | Provider selector/limit 越界 | 否 | fail closed |
| `CAPTURE_PATH_INVALID` | 输出不在 local raw directory | 否 | 不写文件 |
| `AUDIT_SECRET_RISK` | secret field/URL/header value 被检测 | 否 | `replay_ready=false` |
| `AUDIT_LIMIT_INVALID` | capture 数量超过合同 | 否 | `replay_ready=false` |
| `REPLAY_INVALID` | 路径越界或 JSON/capture schema 无效 | 否 | content-free error summary |

## 13. Migration and Rollback

- Migration：无。
- 回滚：删除脚手架与 SPEC；本地 capture 不在 Git，需由用户自行决定保留或安全删除。
- 数据兼容：`capture_version=1`；未来变更必须显式版本化。

## 14. Tasks

- [x] T01 — 定义 SPEC、安全边界和 Provider limits。
- [x] T02 — 添加 dry-run capture scaffold 与本地路径门禁。
- [x] T03 — 添加 content-free audit/replay summary。
- [x] T04 — 添加 mock-only tests。
- [ ] T05 — 用户/ChatGPT Review SPEC 与脚手架。
- [ ] T06 — Review PASS 后另行授权单次真实 capture；本轮不执行。

## 15. 测试要求

- 默认 dry-run 不访问网络；只有 execute path 可使用 `httpx.MockTransport`。
- CLI 不接受 key/token；capture 不保存 secret 或 secret-bearing URL/request。
- Mock raw response structure 被本地写入；stdout 不含内容。
- Marketaux/Finnhub/EIA/SEC limits fail closed；SEC arrays 截断 `<=10`。
- `local_evaluation/` 被 gitignore，且无文件进入 tracking。
- Audit 不含内容/secret，能检测 risk，输出 hash/size/count/field names。
- Audit 空目录以 `no_captures_found` 和 exit code `2` fail closed。
- Replay 不导入网络 client，summary 不含 raw content；正常 capture 为 replay-ready，未知
  Provider 或空 items 不得 replay-ready。

## 16. 验收标准

- [ ] 所有脚本默认无网络，测试全部 mock-only。
- [ ] 真实 capture 请求数为 0。
- [ ] `local_evaluation/` gitignored 且 Git tracking 为空。
- [ ] Secret/raw content safety tests PASS。
- [ ] Provider limit 与 SEC truncation tests PASS。
- [ ] 无 Adapter、DB、migration、schema/ORM、collection、AI API 或投资建议。
- [ ] Foundation validator、Ruff、mypy、pytest、package review PASS。

## 17. Verification Evidence

| Requirement | Evidence | Result | Date |
|---|---|---|---|
| No real capture/API request | mock-only tests and delivery declaration | PASS — zero real requests | 2026-08-01 |
| Foundation | `python3 scripts/validate-foundation.py` | PASS | 2026-08-01 |
| Quality | Ruff / format / mypy / pytest | PASS — 107 tests | 2026-08-01 |
| Package safety | `bash scripts/package-review.sh /tmp/news_collect_spec0006_review.zip` | PASS | 2026-08-01 |

## 18. Commit Evidence

由本 PR 提交记录证明；Review fix commit 在发生时补充。

## 19. 实现结果

SPEC 与脚手架已完成，等待 Review；没有真实 capture。

## 20. 与 SPEC 的偏差

无已批准偏差。

## 21. 已知问题与风险

- Contract/license/retention metadata 仍会阻塞正式 Adapter 与 collection。
- Raw local files 可能包含许可内容和敏感业务数据，用户负责本地访问控制、备份与删除。
- SEC submissions payload 原始范围较大，必须在写盘前截断 recent columns。
- Audit 采用确定性启发式 secret detection，不替代人工审查或专用 secret scanner。
- Replay 目前不 normalization；`normalized_items=0` 是事实状态。

## 22. Review History

| Round | Result | Findings | Resolution |
|---|---|---|---|
| 1 | Pending | Initial scaffold | Pending review |

## 23. 架构与治理检查

- Active Foundation：v2.1-FROZEN。
- 不依赖 Proposed Decision 实现；不需要 Foundation revision。
- 无 schema / existing entity 变化。
- Provider：仅四个 smoke-PASS provider；真实 capture 仍需逐次授权。
- Mode：manual one-shot evaluation capture；无 polling scheduler/streaming/webhook/backfill。
- 未知或未授权能力 fail closed。
