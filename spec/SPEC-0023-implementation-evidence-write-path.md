# SPEC-0023 — Evidence Write Path Implementation

Status：Active — Implementation Review

Phase：Phase 1 — Evidence Write Path Implementation

Foundation：v2.1-FROZEN（unchanged）

Depends on：SPEC-0023 Evidence Write Path Design（Completed — Docs Review approved）

## 1. 目标

实现安全、幂等、可追溯的 `CommonEvidenceEnvelope` → `evidence_items` Write Path。实现只使用既有
`EvidenceItem` ORM 与 PostgreSQL schema，不改变 migration、ORM 或 DB schema，也不接入任何来源、
采集或后续分析流程。

## 2. 实现范围

- 新增 `market_intelligence.evidence.write_path`。
- 定义 `EvidenceWriteStatus`、`EvidenceWriteError`、`EvidenceWriteRequest`、
  `EvidenceWriteOutcome` 与 `EvidenceWriteSummary`。
- 实现只读取 provenance metadata 的 repository 查询和不拥有 outer commit 的 write service。
- 插入前执行 envelope、raw reference 与 provenance pre-check。
- 实现 provider-scoped hash/item ID 幂等和冲突分类。
- 每次 insert 使用 savepoint；批量逐项继续并生成安全、计数守恒的 summary。
- 使用 synthetic/mock-only PostgreSQL tests 验证行为。

## 3. 严格非范围

- 不写 RawItem、ContentItem、Event、Analysis、Recommendation、Notification 或 Outbox。
- 不新增表/model，不修改 Alembic migration、ORM、DB schema、index、constraint 或 enum。
- 不请求 API，不执行 `provider_capture.py --execute`，不读取 `.env`、raw capture 或
  `local_evaluation/`。
- 不实现 Provider Adapter、AdapterRegistry、collection runner、scheduler 或 persistence 接入。
- 不实现正式 normalization、canonicalization、dedup、clustering、Event 或 AI analysis。
- 不实现 investment recommendation、Portfolio/Holding、Telegram 或交易动作。
- 不启动 SPEC-0022；SPEC-0005 X Source and Account Collection 范围不变。

## 4. 输入与输出合同

输入 `EvidenceWriteRequest` 必须包含：

- 一个已经构造的 `CommonEvidenceEnvelope`；
- `source_id`；
- 可选 `source_account_id`；
- 必填 `raw_item_id`；
- 可选 `content_item_id`。

Write Path 不读取 Provider response、SDK、capture 或 collection output。输出只包含 status、opaque
UUID/reference、provider、opaque provider identity/hash 与 allowlisted safe errors，不包含 content、
numeric value、raw payload、external URL、secret 或 SQL parameter。

## 5. 状态合同

`EvidenceWriteStatus`：`inserted`、`existing`、`duplicate`、`blocked`、`invalid`、`failed`。

- 新 row 正常写入：`inserted`；
- 安全等价的 provider-scoped identity 已存在：`existing`，summary 计入 duplicate；
- unsafe raw reference 经安全移除后 row 写入：`blocked`；
- envelope/provenance pre-check 失败：`invalid`；
- identity conflict 或 DB failure：`failed`。

本实现不使用 upsert，不覆盖旧 row。

## 6. raw_payload_reference 规则

安全值：`None`，或以 `internal://`、`capture://`、`local-ref://` 开头且不含 secret marker 的 opaque
reference。

以下值不安全：HTTP(S)、非法 scheme，或大小写不敏感地包含 `api_key=`、`api_token=`、`token=`、
`authorization`、`x-finnhub-token`。

本实现固定策略：

1. 插入前识别 unsafe reference；
2. 不记录、不回显原值或周边文本；
3. 写入值固定替换为 `NULL`；
4. `processing_status = blocked`；
5. row 与 outcome 使用固定错误 `raw_payload_reference_unsafe` / `unsafe_reference_removed`。

DB check 仍是最终安全拦截，不作为业务层静默丢弃策略。

## 7. Provenance pre-check

插入前必须验证：

- `raw_item_id` 存在且 `raw_item.source_id == source_id`；
- RawItem 的 account provenance 与请求 `source_account_id` 完全一致；
- 非空 SourceAccount 存在且属于同一 Source；
- 非空 ContentItem 存在，且其 `raw_item_id`、`source_id`、`source_account_id` 与请求一致。

缺失 reference 返回 `reference_not_found`；关系不一致返回 `provenance_mismatch`。两者均不插入、
不自动改写 provenance，也不回显内容。

## 8. Idempotency 与冲突

### 8.1 provider + provider_item_hash

- 安全 identity/provenance 等价：返回 `existing`，不重复插入；
- hash 相同但 provenance/identity 不一致：`failed` + `provider_hash_conflict`；
- 不执行 cross-provider 或 semantic dedup。

### 8.2 provider + provider_item_id

- `None` 保持 `None`，不得合成 ID；
- ID 已存在且 hash/identity/provenance 等价：返回 `existing`；
- ID 已存在但 hash 不同：`failed` + `provider_item_id_conflict`；
- 不 upsert、不覆盖旧 row。

并发 unique conflict 在 savepoint rollback 后按相同规则重新分类；无法安全分类时使用固定 DB error。

## 9. 事务与错误安全

- service 不提交 outer transaction；commit ownership 属于调用方。
- 每条 insert 使用 nested transaction/savepoint。
- 单条 constraint/DB failure 只回滚该 row，后续 batch item 继续。
- DB exception 不进入 outcome/log；只返回固定 `constraint_rejected` 或 `database_write_failed`。
- unexpected per-row failure 也必须成为安全 failed outcome，不得造成未计数项。
- 不进行 blind retry；调用方未来若处理 transient DB failure，必须复用同一 provider identity。

## 10. Summary 守恒

`EvidenceWriteSummary` 包含 input、inserted、duplicate、blocked、invalid、failed counts、outcomes 和
flattened safe errors。`existing`/`duplicate` 均计入 `duplicate_count`。构造时强制：

```text
input_count = inserted_count + duplicate_count + blocked_count + invalid_count + failed_count
```

不允许 silent rollback、unaccounted input 或 raw error propagation。

## 11. 测试要求

PostgreSQL tests 必须覆盖：

- valid envelope 写入且只增加 `evidence_items`；
- unsafe HTTP(S)/secret marker 各大小写分支被置 `NULL`、标记 blocked 且不泄露；
- raw/account/content provenance not-found 与 mismatch；
- provider hash 与 provider item ID 的 duplicate/conflict 分支；
- nullable provider item ID 不合成；
- batch 单行失败不阻断后续成功，summary counts 守恒；
- DB error 转固定安全错误，不输出 SQL parameter/raw value；
- source audit 禁止 network、capture、Adapter、collection、Event、AI 等依赖；
- outcome/summary dataclass 不暴露 content/value/raw payload 字段。

测试只使用 synthetic metadata 和本地 PostgreSQL semantics，不使用 SQLite、不访问网络、不读取
raw capture/`local_evaluation/`。

## 12. 验收标准

- [x] 只实现 `CommonEvidenceEnvelope` → `evidence_items`。
- [x] unsafe reference 插入前安全移除，blocked/error 行为可测试且无泄露。
- [x] provenance pre-check 与既有 DB constraint 双层防护生效。
- [x] provider-scoped duplicate/conflict 行为确定且无 overwrite。
- [x] per-row savepoint、safe failure 与 summary 守恒通过 PostgreSQL tests。
- [x] 无 migration、ORM、schema、Adapter、collection、dedup/Event/AI 变化。
- [x] Foundation、Ruff、mypy、pytest 与 package review PASS。

## 13. Verification Evidence

| Verification | Result |
|---|---|
| Focused PostgreSQL/source-audit tests | PASS — 22 tests |
| Full pytest regression | PASS — 209 tests；1 existing Alembic deprecation warning |
| Ruff check / format / mypy | PASS |
| Foundation validator | PASS |
| Safe review package | PASS |

未请求 API、执行 capture、读取 raw capture/`local_evaluation/` 或启动 SPEC-0022。

## 14. Review History

| Round | Result | Evidence | Resolution |
|---|---|---|---|
| 1 | IN REVIEW | 本 implementation PR、CI 与 review package | 等待用户/ChatGPT Implementation Review |

## 15. 后续门禁

本 SPEC PASS 前不得开始其他 SPEC。PASS 也不授权 SPEC-0022、Adapter、collection、正式
normalization、dedup、Event 或 AI；任何后续能力必须由用户单独激活和审核。
