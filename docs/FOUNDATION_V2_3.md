# Foundation v2.3 — Pre-AI Collection Readiness

Status：FROZEN — effective

Frozen Date：2026-08-13

Freeze Review：PASS

Reviewed baseline：`4df76e1f0ed9812d962369b9766bf372b102d952`

Previous Foundation：v2.2-FROZEN（historical frozen version；保留且不重写）

## 1. 有限授权

Foundation v2.3-FROZEN 仅允许通过独立 SPEC、Docs/Implementation Review 和用户明确授权，审核：

1. 真实 AI 前的 Pre-AI collection reliability；
2. `CollectionTarget` 与 target-owned state；
3. unified production collection control plane；
4. scheduler/control-plane rewrite；
5. collection 与 Telegram/Event delivery 解耦；
6. durable safe projection；
7. 经独立 SPEC 完整审核的 Provider/source operation readiness；
8. R8 Event/Evidence/Fact completeness。

R0 PASS 只改变可审核的 Foundation ceiling，不创建 Active SPEC，不授权代码、migration、schema、
runtime config、Provider/Telegram/AI 请求或 bounded live verification，也不自动启动 R1。

## 2. 继续冻结的边界

- 系统保持单用户、私人部署；不引入 tenant、workspace、团队、计费或公开转载产品。
- Broad Scan 与 Controlled Push 保持独立。
- U.S. equities、U.S. ETFs、Crypto 和 related cash positions 保持直接 market/portfolio scope；宏观、
  能源、监管、债券、FX 与商品仍是解释性输入，除非后续 Foundation Revision 改变边界。该范围不
  授权 Portfolio/Holding/Investment Plan implementation。
- credential 仅能在获批 worker runtime 中注入，不得进入 DB、task/config payload、日志或审核材料。
- access、license、robots、retention、attribution、redistribution 与 provenance 边界继续生效。
- 禁止未授权抓取、访问控制/付费墙绕过、自动交易、BUY/SELL/HOLD、仓位或投资建议。
- 真实 AI、Market Validation、Recommendation、Portfolio/Holding/Investment Plan 仍未授权。

## 3. R1–R8 门禁

R1–R8 每一步均须独立 SPEC、Docs/Implementation Review 和明确授权。R7 只允许为符合现有 Collection
Scope 与直接市场解释需要的 Company IR、official RSS、政府、宏观或监管官方来源准备独立审核；
它不是自动实现、请求或 production activation 许可。每个 endpoint family 仍须独立通过 identity、
access/license/robots/retention/attribution、typed contract、budget/cursor/revision/recovery、mock/
integration、用户授权 bounded live 与 production activation review。越出现有范围须新的 Foundation
Revision。

## 4. PR #39 / SPEC-0040

PR #39 必须在 R0–R8 完成前保持 Draft、不得合并。之后必须基于届时最新 `main` 重新审计和 rebase；
其 AI contract、Fact digest、snapshot 与 routing 设计不预先保证保留。Alembic revision 必须按最终串行
合并顺序处理，禁止双 head、复制 revision 或重写已发布历史。

## 5. 审计链

- Candidate draft：`docs/FOUNDATION_V2_3_DRAFT.md`
- Freeze Review package：`docs/FOUNDATION_V2_3_FREEZE_REVIEW.md`
- Previous effective version：`docs/FOUNDATION_V2_2.md`
- Readiness program：`docs/PRE_AI_COLLECTION_READINESS.md`
