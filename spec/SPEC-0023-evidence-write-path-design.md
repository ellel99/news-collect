# SPEC-0023 — Evidence Write Path Design

Status：Completed — Docs Review approved

Phase：Phase 1 — Evidence Write Path Design Only

Foundation：v2.1-FROZEN（unchanged）

Depends on：SPEC-0019、SPEC-0020、SPEC-0021 implementation（Completed）

## 1. 目标

设计如何把已经通过纯合同与 provider mapping 边界生成的 `CommonEvidenceEnvelope` 安全、幂等、
可追溯地写入既有 `evidence_items`。本文只定义未来 Write Path 的输入、验证、事务、冲突、错误与
结果合同，不实现 Python、repository、service、migration、ORM 或任何运行时接入。

## 2. 严格非范围

- 不写 Python implementation、repository、service、unit of work 或 persistence code。
- 不修改 Alembic migration、ORM、DB schema、index、constraint 或 enum。
- 不请求 API，不执行 `provider_capture.py --execute`，不读取或 replay raw capture/
  `local_evaluation/`。
- 不实现 Provider Adapter、AdapterRegistry、collection integration 或 scheduler。
- 不实现正式 normalization、canonicalization、dedup、clustering 或 Event generation。
- 不实现 AI analysis、investment recommendation、Telegram push 或交易动作。
- 不激活 SPEC-0022；SPEC-0005 X Source and Account Collection 范围保持不变。

## 3. 输入合同

Write Path 的唯一业务输入是 `CommonEvidenceEnvelope`：

- 可由 `provider_mappings.py` 的纯 mapping output 产生；
- 调用方还必须显式提供已存在的 `source_id`、可选 `source_account_id`、`raw_item_id` 与可选
  `content_item_id` provenance references；
- 输入不来自实时 API，也不允许 Write Path 直接读取 Provider SDK response、
  `local_evaluation/` 或 raw capture；
- 输入不得包含真实 title、body、URL、snippet、description、quote/EIA value、SEC
  accessionNumber/primaryDocument value 或 raw payload；
- 未知 provider/item type、非法 shape 或 content/secret-bearing input 必须 fail closed。

`provider_mappings.py` 只是已完成的 pure mapping scaffold；本 SPEC 不把它接入 collection 或正式
normalization pipeline。

## 4. 输出合同

未来 Write Path 只允许：

- 插入或识别既有 `evidence_items` row；
- 返回单条 `EvidenceWriteOutcome` 或批量 `EvidenceWriteSummary` 的安全结构结果；
- 返回 allowlisted safe errors，不返回 rejected raw value。

不得写入或创建 RawItem、ContentItem、Event、Analysis、Recommendation、Notification 或 Outbox，
不得触发 Telegram。RawItem/ContentItem 只能作为已存在的 FK reference 使用。

## 5. 插入前验证顺序

未来实现必须在开启 row insert/savepoint 前按确定顺序执行：

1. 验证 envelope version、provider、provider item type、evidence kind、source type 与 flags；
2. 验证 JSON presence/ref/error shape 只含已批准结构，不含 content/value/secret；
3. 验证 `raw_payload_reference`；
4. 验证 Source/SourceAccount/RawItem/ContentItem provenance；
5. 计算或验证 provider-scoped identity/hash，但不执行 cross-provider dedup；
6. 进入单 row transaction/savepoint 并处理唯一冲突或 DB fail-closed error。

应用层 pre-check 不能替代 DB constraint；DB constraint 也不能替代应用层安全验证。

## 6. raw_payload_reference 安全合同

### 6.1 已有 DB 防线

`evidence_items` 已只允许 `internal://`、`capture://` 或 `local-ref://` opaque reference，并拒绝
HTTP(S)。数据库还会不区分大小写地拒绝包含以下 marker 的 reference：

- `api_key=`；
- `api_token=`；
- `token=`；
- `authorization`；
- `x-finnhub-token`。

这些 checks 是最终数据库安全拦截，不是业务层清洗或数据丢弃策略。

### 6.2 Write Path 预处理

- Write Path 不得依赖 DB 报错作为主要 sanitize 机制；插入前必须 validate/sanitize。
- 安全 reference 可原样写入。
- 不安全 reference 不得原样写入、记录或回显。
- 如果调用方能提供不含 secret/content 的稳定内部定位，应生成新的安全 opaque reference；否则
  使用 `raw_payload_reference = NULL`。
- row 必须追加 safe error code `raw_payload_reference_unsafe`，且 `processing_status` 设为
  `blocked` 或 `invalid`；具体选择由未来实现 SPEC 的状态转换表最终批准。
- safe error 只能标识字段和类别，不得包含 secret marker 周边原文、完整 URL 或 raw value。

sanitize 不是删除 RawItem，也不是丢弃 envelope；安全转换后的 row 仍应保留 provenance。若无法在
不扩大许可或泄露秘密的前提下形成合法 row，则进入 safe failure report。

## 7. 防止静默丢数据

- DB rejection 不是业务层数据丢弃策略。
- 单条 evidence 失败不得导致整批静默 rollback，也不得被计为成功或 duplicate。
- 批量写入必须使用 per-row savepoint 或具有同等隔离与继续处理语义的机制。
- 每个成功项提交为 inserted/existing outcome；失败项进入 safe failure report。
- `raw_item_id` 必须保留在内部追溯结果中；对外日志只允许 opaque ID，不解析 RawItem payload。
- failure report 不得包含 secret、raw content、Provider value、完整 URL 或 raw payload。
- 批次结束必须核对：`input_count = inserted_count + duplicate_count + blocked_count +
  invalid_count + failed_count`，不允许 unaccounted item。

## 8. Provenance 校验

Write Path 必须确保 envelope/call context 的 `source_id`、`source_account_id`、`raw_item_id` 与可选
`content_item_id` 属于同一 provenance chain：

- 读取 RawItem 的 `source_id` 并与请求 `source_id` pre-check；
- `source_account_id` 非空时，验证 account 属于同一 Source，并与 RawItem provenance 一致；
- `content_item_id` 非空时，验证其 RawItem/Source 与请求一致；
- DB 已通过 `evidence_items(raw_item_id, source_id)` → `raw_items(id, source_id)` composite FK
  强制关键一致性；Write Path 仍必须在插入前检查并生成安全错误；
- mismatch 使用 `provenance_mismatch`，归类为 invalid/failed outcome，不得静默丢弃或尝试改写
  Source/RawItem 关系。

## 9. Idempotency 与冲突处理

### 9.1 provider + provider_item_hash

- 冲突时读取既有 row 的 opaque identity/provenance metadata 做安全等价检查；
- 等价重复返回 `existing` / duplicate outcome，不重复插入；
- 同 hash 但 provenance 或 contract identity 不一致时 fail closed，返回
  `provider_hash_conflict`，不得覆盖既有 row。

### 9.2 provider + provider_item_id

- nullable ID 缺失时不得合成虚假 ID；由 hash 唯一性承担 provider-scoped idempotency；
- 非空 ID 冲突且安全 identity 等价时返回 existing/duplicate；
- ID 相同但 hash 不同表示可能 revision/contract conflict，返回 `provider_item_id_conflict`，不得
  upsert 覆盖或静默接受。

本设计不做 cross-provider identity、semantic dedup、clustering 或 Event generation。

## 10. 事务策略

### 10.1 单条写入

- pre-check 通过后，在调用方明确的 transaction scope 内尝试插入；
- unique conflict 只按第 9 节映射为 duplicate 或 fail-closed conflict；
- check/FK/shape violation 不自动重试，转换为 allowlisted safe failure；
- transient DB availability/deadlock 可由上层按固定上限重试，但不得重复写入或泄露 SQL values。

### 10.2 批量写入

- batch transaction 内每条使用 savepoint；单 row constraint failure 只回滚该 savepoint；
- 成功 row 保留，后续 row 继续处理；
- transaction-level fatal error 时必须返回明确 batch failure，不能声称部分成功已提交；
- implementation 必须明确 outer transaction 的 commit ownership 和 retry identity。

### 10.3 结果摘要

`EvidenceWriteSummary` 至少包含：

- `input_count`；
- `inserted_count`；
- `duplicate_count`；
- `blocked_count`；
- `invalid_count`；
- `failed_count`；
- `safe_errors`（code + field + safe_message key + opaque provenance reference only）。

计数必须守恒且可测试。summary 不包含 raw input、content/value、SQL parameter 或 secret。

## 11. 错误与日志合同

### 11.1 Error code allowlist candidate

- `raw_payload_reference_unsafe`；
- `provenance_mismatch`；
- `provider_hash_conflict`；
- `provider_item_id_conflict`；
- `envelope_invalid`；
- `reference_not_found`；
- `constraint_rejected`；
- `database_temporarily_unavailable`；
- `database_write_failed`。

未来实现 SPEC 必须最终批准 code → outcome/status/retry mapping，不得临时拼接新 code。

### 11.2 Safe message allowlist

日志和 failure report 只允许固定 message key，例如 `unsafe_reference_replaced`、
`provenance_check_failed`、`existing_evidence_returned`、`database_constraint_rejected`。禁止记录：

- raw title/body/URL/snippet/description；
- quote value、EIA value；
- accessionNumber、primaryDocument 或 filing value；
- API key、token、Authorization；
- raw payload、完整 response 或完整 external URL；
- DB exception 中可能包含的 SQL parameter/raw value。

日志可包含 correlation ID、provider code、opaque row/raw item ID、outcome、safe error code 和 counts。

## 12. 可恢复与 Fail-closed 分类

| Condition | Outcome | Retry |
|---|---|---|
| Exact provider-scoped duplicate | duplicate/existing | no |
| Unsafe raw reference，可安全替换/null | blocked/invalid row with safe error | no |
| Provenance mismatch | invalid/failed | no |
| Unknown contract/shape/check violation | invalid | no |
| Conflicting provider ID/hash identity | failed | reviewer decision, no automatic overwrite |
| Transient DB unavailable/deadlock | failed | bounded retry by caller |
| Unknown DB error | failed, fail closed | no blind retry |

任何 retry 必须复用 provider-scoped identity，不能扩大批次或触发 collection。

## 13. 未来实现测试门禁

实现授权前必须批准 mock/synthetic-only tests，至少覆盖：

- 安全/不安全 raw reference sanitize 与无 secret 输出；
- provenance pre-check 和 DB composite FK 双层拒绝；
- 两个 provider-scoped unique conflict 的 duplicate/conflict 分支；
- per-row savepoint：一个失败不阻断后续成功，summary 计数守恒；
- DB exception/日志 redaction，不输出 content/value/secret/SQL parameters；
- nullable content relation 与 non-content evidence；
- no writes to RawItem/ContentItem/Event/Analysis/Recommendation/Notification/Outbox；
- no network、Provider、capture、Adapter、collection、dedup/Event/AI dependency。

PostgreSQL 语义不可由 SQLite 替代。

## 14. 验收标准（Docs Review）

- [x] 输入/输出和单表写入边界经 Reviewer 批准。
- [x] unsafe reference 预校验、安全替换/null、status/error 行为经 Reviewer 批准。
- [x] provenance、idempotency/conflict、savepoint 与 summary 守恒规则经 Reviewer 批准。
- [x] error/log allowlist 和禁止输出边界经 Reviewer 批准。
- [x] 明确 docs-only，无 migration、ORM、repository/service 或运行时集成。
- [x] SPEC-0022 未激活，Foundation v2.1-FROZEN 未修改。

## 15. Verification Evidence

PR #22 已通过 Docs Review 并合并。该 Review 只批准 Write Path 设计，没有请求 API、读取 raw
capture/`local_evaluation/` 或实现 Write Path。

## 16. 后续门禁

Docs Review PASS 只批准设计。用户随后单独授权
`spec/SPEC-0023-implementation-evidence-write-path.md`，其实现范围仅为
`CommonEvidenceEnvelope` → `evidence_items`；不得据此开始 SPEC-0022、Adapter、collection、正式
normalization、dedup、Event 或 AI。

## 17. Review History

| Round | Result | Evidence | Resolution |
|---|---|---|---|
| 1 | PASS | PR #22、CI、Foundation validator 与 docs review package | Docs Review approved and merged；只批准 Write Path 设计 |
