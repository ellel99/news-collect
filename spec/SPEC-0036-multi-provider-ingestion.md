# SPEC-0036 — Multi-provider Ingestion for Finnhub, EIA and SEC EDGAR

Status：Active — Implementation Review

Phase：Phase 1 — Multi-provider Ingestion

Foundation：v2.1-FROZEN（unchanged）

Depends on：SPEC-0035（Completed）

## 1. 目标与实现范围

一次性把 Finnhub、EIA Open Data、SEC EDGAR 接入现有 provider-neutral collection/evidence 链路：

`ProviderAdapter → CollectionRunner → RawItem → sanitized projection → CommonEvidenceEnvelope → EvidenceWriteService → evidence_items`

- 实现 `FinnhubAdapter`：单 symbol quote，limit 固定 1；只生成 market-data evidence，不做市场验证。
- 实现 `EiaAdapter`：electricity retail-sales monthly rows，limit 1–5；只生成 official-energy evidence。
- 实现 `SecEdgarAdapter`：submissions/recent filing metadata，limit 1–10；不下载 filing body；额外生成
  metadata-only `ContentItem` 作为可见官方披露 feed candidate。
- 扩展既有 `HttpxProviderTransport` allowlist，不引入 Provider SDK。
- 扩展既有 projection sidecar、trigger、mapper dispatch 与 EvidenceWriteService 路径，不复制 DB write。
- 三个 manual smoke 默认 dry-run；只有显式 `--execute` 才读取 process environment、访问 runtime。
- 三个 smoke 均提供 `--doctor` 与 `--bootstrap-target`；doctor 只返回 Source/SourceAccount counts、
  eligibility 与固定 safe error，bootstrap 只创建缺失的最小 target，且不读 Provider credential、不请求 API。

## 2. 安全与数据边界

- Finnhub credential 仅为 process `FINNHUB_API_KEY`；EIA 仅为 process `EIA_API_KEY`。
- SEC 使用 process `SEC_USER_AGENT` + `SEC_CONTACT_EMAIL` 合成合规 User-Agent；不记录真实值。
- 不读取 `.env`；不允许 CLI 传 key/token；不输出完整 request URL、Authorization 或 credential。
- 不保存 raw response、quote values、EIA values、filing body、primaryDocument body 或大 payload。
- RawItem 只保存 internal opaque payload reference/hash、identity、status、timestamps 与 retention class。
- evidence mapping 只使用 sanitized presence/identity projection；写入必须经过 EvidenceWriteService。
- smoke summary 只包含 provider、状态、limit、counts、booleans 与固定 safe errors。
- tests/CI/package review 仅使用 `MockProviderTransport`，不得请求真实 Provider。

## 3. Cursor、幂等与 ContentItem

- Finnhub cursor：quote timestamp + source-scoped item identity。
- EIA cursor：period + geography + sector identity；不做 historical backfill 或自动分页。
- SEC cursor：filing date + accession number；不下载 filing document。
- RawItem 幂等继续由既有 source/external-id/payload contract 处理；evidence 幂等继续由 provider-scoped
  hash/ID constraints 与 EvidenceWriteService 处理。
- Finnhub/EIA 为数值/官方证据，不创建伪新闻 ContentItem。
- SEC 仅创建 metadata-only OFFICIAL_RELEASE ContentItem，title 为 form/ticker 标签，不保存 filing body 或 URL。

## 4. Manual bounded smoke

Target doctor/bootstrap（不读 Provider credential、不请求 API）：

```bash
python3 scripts/finnhub_ingestion_smoke.py --doctor
python3 scripts/finnhub_ingestion_smoke.py --bootstrap-target
python3 scripts/eia_ingestion_smoke.py --doctor
python3 scripts/eia_ingestion_smoke.py --bootstrap-target
python3 scripts/sec_edgar_ingestion_smoke.py --doctor
python3 scripts/sec_edgar_ingestion_smoke.py --bootstrap-target
```

- 缺失 target 返回 `provider_target_missing`；多个 eligible target 返回
  `provider_target_not_unique` 并 fail closed。
- disabled/unauthorized Source 与 missing account 分别返回固定 safe error；不输出行内容或配置值。
- 首次 bootstrap 返回 `created`，重复执行返回 `already_exists`，之后 doctor 返回 `PASS`。
- bootstrap defaults 只含 `AAPL` symbol、`electricity` dataset，或 SEC `AAPL`/公开 CIK；credential
  与 SEC contact 永不写入 SourceAccount。

默认 inert dry-run：

```bash
python3 scripts/finnhub_ingestion_smoke.py
python3 scripts/eia_ingestion_smoke.py
python3 scripts/sec_edgar_ingestion_smoke.py
```

真实 execute 只允许用户单独授权后手动运行；本 PR 不执行：

```bash
FINNHUB_API_KEY=... python3 scripts/finnhub_ingestion_smoke.py --execute --symbol AAPL --limit 1
EIA_API_KEY=... python3 scripts/eia_ingestion_smoke.py --execute --dataset electricity --limit 1
SEC_USER_AGENT=... SEC_CONTACT_EMAIL=... \
  python3 scripts/sec_edgar_ingestion_smoke.py --execute --ticker AAPL --limit 1
```

## 5. 明确非目标

- 不修改 scheduler 或 Telegram routing。
- 不实现 AI、投资建议、market validation analysis、energy analysis。
- 不实现 formal dedup、Event、clustering、Finnhub/EIA/SEC 自动调度。
- 不实现 filing body download/full parsing，不保存 article/body/large payload。
- 不修改 migration、ORM 或 DB schema；不启动 SPEC-0022。

## 6. 验收与测试

- [x] 三 Provider mocked success → RawItem + provider-specific evidence_items。
- [x] SEC mocked success → metadata-only ContentItem；Finnhub/EIA 不创建伪 feed content。
- [x] 三 Provider malformed/error responses fail closed，不写 RawItem/evidence。
- [x] 默认 smoke 不读 credential、不访问 network/DB、输出 safe JSON。
- [x] adapter contract、limits、cursor、secret separation 由 mock tests 覆盖。
- [x] source audit 不含 scheduler/Telegram/AI/recommendation/Event/clustering/local capture 依赖。
- [x] 三 Provider missing → bootstrap created → already_exists → doctor PASS。
- [x] 多 target fail closed；bootstrap 不读 credential、不调用 transport、不保存 secret config。
- [x] SEC runtime 缺 agent/contact 任一项即 fail closed；两者齐全时只在 transport 层构造 User-Agent，
  summary/repr/error 不回显。
- [ ] CI、完整 PostgreSQL suite 与 review package PASS。

## 7. Review History

| Round | Result | Evidence | Resolution |
|---|---|---|---|
| 1 | IN REVIEW | implementation diff、mock/PostgreSQL tests、dry-run summaries、review package | 等待用户/ChatGPT Review |
