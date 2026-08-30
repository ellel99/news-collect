# M2-A Provider Operation / Cursor Matrix

Status: Implementation Review — no production activation

All expanded rows use config version 2 / provider contract 2, projection schema 1 and continuation version 1.
The exact tuple is resolved by the static registry; config hashes fence continuation to the reviewed target.

| Provider / operation | Request and continuation | Typed factual/content policy |
|---|---|---|
| Marketaux / news_all | `/v1/news/all`; explicit query/language/symbols/start/end; ascending page+limit, max31-day window | Safe title/URL/source/time/query/language/symbols; ARTICLE when complete; link_only |
| Finnhub / quote (v1) | `/api/v1/quote`; explicit symbol; one observation is the endpoint's semantic unit | Real seven quote values/timestamp/currency/exchange; licensed; no Content/Notification |
| Finnhub / company_news | `/api/v1/company-news`; symbol/from/to, max31 days; stable time/identity keyset, not provider pagination | Provider-global provider ID or normalized validated-URL fallback (symbol is observation context, not canonical identity); headline/time/URL/source/category; licensed ARTICLE only when safe; summary blocked |
| EIA / electricity_retail_sales | `/v2/electricity/retail-sales/data/`; explicit geography/sector facets, monthly window; offset/length | Stable geography/sector/price series, period/value/unit; public_summary; no Content/Notification |
| EIA / electricity_rto_region_data | `/v2/electricity/rto/region-data/data/`; explicit respondents, D/NG types, hourly start/end ≤7 days; offset/length | Stable region/type series and per-period identity; finite value/unit; unknown unit is PARTIAL; no Content/Notification |
| SEC / submissions_recent | `data.sec.gov/submissions/CIK....json`; explicit CIK/ticker/forms/date window ≤31 days; bounded safe official historical files ≤5 | Accession canonical identity, document/official Archives URL and submissions file provenance; OFFICIAL_RELEASE, UNAVAILABLE, link_only |

SEC follows only validated `CIK{same_cik}-submissions-NNN.json` references from the submissions response.
The form set is explicitly selected from 8-K/10-Q/10-K/6-K. Unknown forms in config fail closed; unrelated returned
forms are filtered before bounded keyset selection. Each file has its own filing-date/accession key. No filing
documents are fetched. Append/reorder do not invalidate continuation; earlier arrivals and emitted-key revisions
are discovered by subsequent bounded overlap reconciliation.

All v2 configs require fixed_window or rolling_window. Rolling lookback/overlap/lag and day/hour/month granularity
resolve and persist before the first request; exact target/config/operation/contract/cursor/run-mode lineage lets
retry/crash/lock-loss/stale recovery reuse the frozen window. v2 legacy_cursor_type is NULL and
must exactly equal registry mapping. Production rollback-window activation protection remains enforced.
Retail uses explicit month-unit parameters (lookback ≤12 complete inclusive periods, overlap < lookback,
lag ≤12 months), not seconds. Lag zero excludes the current incomplete month.

Batch ceiling 100 is a code ceiling, not a claim about account entitlement. Provider plan/quota/license review may
require lower target limits. Every run independently enforces ≤20 requests/pages, ≤10 MB decoded response,
configured request timeout, and ≤900 seconds runtime. No live entitlement or coverage has been verified here.
