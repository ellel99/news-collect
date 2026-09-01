# ruff: noqa: E501
from __future__ import annotations

import json
import os
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest
import pytest_asyncio
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from market_intelligence.collection.adapter_factory import UnifiedAdapterFactory
from market_intelligence.collection.control_plane import (
    CollectionControlPlaneWorker,
    TargetDispatch,
    TargetScheduler,
    dispatch_identity,
)
from market_intelligence.collection.control_plane_tools import ControlPlaneAuditService
from market_intelligence.collection.target_configs import build_operation_registry
from market_intelligence.collection.target_repository import TargetRepository, TargetRepositoryError
from market_intelligence.db.base import system_metadata
from market_intelligence.db.models import (
    AuditLog,
    CollectionCursor,
    CollectionRun,
    CollectionRunMode,
    CollectionTarget,
    ContentItem,
    EvidenceItem,
    Notification,
    RawItem,
    RawItemObservation,
    SafeFactProjection,
)
from market_intelligence.evidence.handoff import EvidenceProjectionHandoffWorker
from market_intelligence.notifications.intent import (
    WATERMARK_KEY,
    IntentWatermark,
    NotificationIntentReconciler,
    _persist_cutover_watermark,
)
from market_intelligence.providers.breadth import BreadthAdapter
from market_intelligence.providers.breadth_config import breadth_config
from market_intelligence.providers.continuation import (
    ContinuationContractError,
    decode_continuation,
    encode_continuation,
    request_lineage,
)
from market_intelligence.providers.contracts import ProviderFetchRequest, ProviderTransportResponse
from market_intelligence.providers.credentials import RuntimeCredential
from market_intelligence.providers.transport import MockProviderTransport
from market_intelligence.providers.windows import resolve_window
from market_intelligence.safe_projection.worker import SafeFactProjectionWorker


def rolling_config(operation="news_all"):
    base = dict(CONFIGS["marketaux", operation])
    base.pop("start")
    base.pop("end")
    return {
        **base,
        "window_mode": "rolling_window",
        "lookback_seconds": 2 * 86400,
        "overlap_seconds": 86400,
        "ingestion_lag_seconds": 0,
        "granularity": "day",
    }


def rolling_operation_config(provider, operation):
    base = {
        k: v
        for k, v in CONFIGS[provider, operation].items()
        if k not in {"start", "end", "window_mode"}
    }
    if operation == "electricity_retail_sales":
        return {
            **base,
            "window_mode": "rolling_window",
            "lookback_months": 2,
            "overlap_months": 1,
            "ingestion_lag_months": 0,
            "granularity": "month",
        }
    granularity = "hour" if operation == "electricity_rto_region_data" else "day"
    unit = 3600 if granularity == "hour" else 86400
    return {
        **base,
        "window_mode": "rolling_window",
        "lookback_seconds": 2 * unit,
        "overlap_seconds": unit,
        "ingestion_lag_seconds": 0,
        "granularity": granularity,
    }


def empty_page(provider, operation):
    if provider == "marketaux":
        return {"data": [], "meta": {"found": 0}}
    if provider == "finnhub":
        return []
    if provider == "eia":
        return {"response": {"data": [], "total": 0}}
    return {
        "filings": {
            "recent": {
                "accessionNumber": [],
                "filingDate": [],
                "form": [],
                "primaryDocument": [],
            },
            "files": [],
        }
    }


DB = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://market_intelligence:local_dev_only@localhost:5432/market_intelligence",
)
NOW = datetime(2026, 1, 3, tzinfo=UTC)
CONFIGS = {
    ("marketaux", "news_all"): {
        "query": "semiconductor",
        "language": "en",
        "symbols": ["NVDA"],
        "start": "2026-01-01",
        "end": "2026-01-03",
    },
    ("finnhub", "company_news"): {"symbol": "NVDA", "start": "2026-01-01", "end": "2026-01-03"},
    ("eia", "electricity_rto_region_data"): {
        "regions": ["CAL", "TEX"],
        "types": ["D", "NG"],
        "frequency": "hourly",
        "start": "2026-01-01T00",
        "end": "2026-01-02T00",
    },
    ("eia", "electricity_retail_sales"): {
        "geographies": ["CA"],
        "sectors": ["ALL"],
        "frequency": "monthly",
        "start": "2026-01-01",
        "end": "2026-01-01",
    },
    ("sec_edgar", "submissions_recent"): {
        "ticker": "NVDA",
        "cik": "0001045810",
        "forms": ["8-K", "10-Q", "10-K", "6-K"],
        "start": "2026-01-01",
        "end": "2026-01-03",
        "max_history_files": 2,
    },
}
for _config in CONFIGS.values():
    _config["window_mode"] = "fixed_window"
CREDENTIALS = {
    "marketaux": "MARKETAUX_API_TOKEN",
    "finnhub": "FINNHUB_API_KEY",
    "eia": "EIA_API_KEY",
    "sec_edgar": "SEC_USER_AGENT",
}


@pytest_asyncio.fixture(autouse=True)
async def future_v2_activation_test_boundary():
    """Local fixture only: exercise v2 worker after the separately gated rollback window.

    Production Migration A guard remains installed/enabled; no Migration B is implemented.
    CI runs serially on its disposable database. Restore the guard even on test failure.
    """
    engine = create_async_engine(DB)
    async with engine.begin() as session:
        await session.execute(
            text(
                "ALTER TABLE collection_targets DISABLE TRIGGER trg_r1_active_legacy_identity_guard"
            )
        )
    try:
        yield
    finally:
        async with engine.begin() as session:
            await session.execute(
                text(
                    "ALTER TABLE collection_targets ENABLE TRIGGER trg_r1_active_legacy_identity_guard"
                )
            )
        await engine.dispose()


def response(body, status=200):
    return ProviderTransportResponse(status_code=status, received_at=NOW, body=body)


def news(identity):
    return {
        "uuid": identity,
        "published_at": "2026-01-02T00:00:00+00:00",
        "title": "Synthetic news",
        "url": f"https://example.com/{identity}",
        "source": {"name": "Example"},
    }


def rows(provider, operation):
    if provider == "marketaux":
        return {"data": [news("a"), news("b")], "meta": {"found": 2}}
    if provider == "finnhub":
        return [
            {
                "id": i,
                "datetime": 1767312000,
                "headline": "Synthetic company news",
                "url": f"https://example.com/{i}",
                "source": "Example",
                "category": "company",
            }
            for i in (1, 2)
        ]
    if provider == "eia" and operation == "electricity_retail_sales":
        return {
            "response": {
                "data": [
                    {
                        "period": "2026-01",
                        "stateid": "CA",
                        "sectorid": "ALL",
                        "price": 12.5,
                        "price-units": "cents per kWh",
                    }
                ],
                "total": 1,
            }
        }
    if provider == "eia":
        return {
            "response": {
                "data": [
                    {
                        "period": "2026-01-01T01",
                        "respondent": r,
                        "type": t,
                        "value": 123.5,
                        "value-units": "megawatthours",
                    }
                    for r, t in (("CAL", "D"), ("TEX", "NG"))
                ],
                "total": 2,
            }
        }
    return {
        "filings": {
            "recent": {
                "accessionNumber": ["0001045810-26-000001"],
                "filingDate": ["2026-01-02"],
                "form": ["8-K"],
                "primaryDocument": ["report.htm"],
            },
            "files": [],
        }
    }


def request(provider, operation, limit=2):
    return ProviderFetchRequest(
        source_id=uuid4(),
        source_account_id=None,
        cursor=None,
        config=CONFIGS[provider, operation],
        limit=limit,
        deadline_at=datetime.now(UTC) + timedelta(seconds=60),
        correlation_id="synthetic",
        target_id=uuid5(NAMESPACE_URL, f"m2a-test:{provider}:{operation}"),
        config_revision=1,
        operation_config_version=2,
        provider_contract_version=2,
        cursor_version=1,
        run_mode="normal",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("provider,operation", CONFIGS)
async def test_operation_adapter_contract(provider, operation):
    transport = MockProviderTransport([response(rows(provider, operation))])
    adapter = BreadthAdapter(
        provider, operation, RuntimeCredential(CREDENTIALS[provider], "synthetic-only")
    )
    result = await adapter.fetch(request(provider, operation), transport)
    assert not result.safe_errors
    assert result.raw_items and len(result.raw_items) == len(result.factual_projections)
    assert len(transport.calls) == 1
    assert all("summary" not in p and "body" not in p for p in result.factual_projections)


def duplicate_page(provider, operation, *, conflict=False):
    body = deepcopy(rows(provider, operation))
    if provider == "marketaux":
        duplicate = deepcopy(body["data"][0])
        if conflict:
            duplicate["title"] = "Conflicting synthetic title"
        body["data"] = [body["data"][0], duplicate]
        body["meta"]["found"] = 2
    elif provider == "finnhub":
        duplicate = deepcopy(body[0])
        if conflict:
            duplicate["headline"] = "Conflicting synthetic headline"
        body = [body[0], duplicate]
    elif provider == "eia":
        duplicate = deepcopy(body["response"]["data"][0])
        if conflict:
            duplicate["price" if operation == "electricity_retail_sales" else "value"] = 999
        body["response"]["data"] = [body["response"]["data"][0], duplicate]
        body["response"]["total"] = 2
    else:
        recent = body["filings"]["recent"]
        for field in ("accessionNumber", "filingDate", "form", "primaryDocument"):
            recent[field] = [recent[field][0], recent[field][0]]
        if conflict:
            recent["primaryDocument"][1] = "revision.htm"
    return body


def sec_history_duplicate_page(*, conflict=False):
    body = deepcopy(rows("sec_edgar", "submissions_recent")["filings"]["recent"])
    for field in ("accessionNumber", "filingDate", "form", "primaryDocument"):
        body[field] = [body[field][0], body[field][0]]
    if conflict:
        body["primaryDocument"][1] = "revision.htm"
    return body


def operation_request(provider, operation, path):
    base = request(provider, operation)
    if path != "sec_history":
        return base
    lineage = request_lineage(base, operation)
    continuation = encode_continuation(
        provider,
        operation,
        base.config,
        lineage,
        {"file": "CIK0001045810-submissions-001.json", "files": []},
    )
    return replace(base, continuation=continuation)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider,operation,path",
    [
        ("marketaux", "news_all", "marketaux"),
        ("finnhub", "company_news", "finnhub"),
        ("eia", "electricity_retail_sales", "eia_retail"),
        ("eia", "electricity_rto_region_data", "eia_rto"),
        ("sec_edgar", "submissions_recent", "sec_recent"),
        ("sec_edgar", "submissions_recent", "sec_history"),
    ],
)
async def test_same_page_duplicate_identity_is_deterministic(provider, operation, path):
    body = (
        sec_history_duplicate_page()
        if path == "sec_history"
        else duplicate_page(provider, operation)
    )
    adapter = BreadthAdapter(
        provider, operation, RuntimeCredential(CREDENTIALS[provider], "synthetic")
    )
    result = await adapter.fetch(
        operation_request(provider, operation, path), MockProviderTransport([response(body)])
    )
    assert not result.safe_errors
    assert len(result.raw_items) == len(result.factual_projections) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider,operation,path",
    [
        ("marketaux", "news_all", "marketaux"),
        ("finnhub", "company_news", "finnhub"),
        ("eia", "electricity_retail_sales", "eia_retail"),
        ("eia", "electricity_rto_region_data", "eia_rto"),
        ("sec_edgar", "submissions_recent", "sec_recent"),
        ("sec_edgar", "submissions_recent", "sec_history"),
    ],
)
async def test_same_page_identity_conflict_fails_closed(provider, operation, path):
    body = (
        sec_history_duplicate_page(conflict=True)
        if path == "sec_history"
        else duplicate_page(provider, operation, conflict=True)
    )
    adapter = BreadthAdapter(
        provider, operation, RuntimeCredential(CREDENTIALS[provider], "synthetic")
    )
    result = await adapter.fetch(
        operation_request(provider, operation, path), MockProviderTransport([response(body)])
    )
    assert result.safe_errors
    assert result.safe_errors[0].safe_message == "breadth_response_invalid"
    assert not result.safe_errors[0].retryable
    assert not result.raw_items and result.continuation is None


@pytest.mark.asyncio
@pytest.mark.parametrize("offset", [99_999, 100_000, 100_001, 9_999_999, 10_000_000])
async def test_eia_offset_policy_accepts_exact_bounded_values(offset):
    provider, operation = "eia", "electricity_retail_sales"
    req = request(provider, operation, 1)
    continuation = encode_continuation(
        provider,
        operation,
        req.config,
        request_lineage(req, operation),
        {"offset": offset},
    )
    result = await BreadthAdapter(
        provider, operation, RuntimeCredential("EIA_API_KEY", "synthetic")
    ).fetch(
        replace(req, continuation=continuation),
        MockProviderTransport([response({"response": {"data": [], "total": offset}})]),
    )
    assert not result.safe_errors
    assert not result.has_more and result.continuation is None


@pytest.mark.asyncio
@pytest.mark.parametrize("offset", [0, 10_000_001])
async def test_eia_offset_policy_rejects_outside_continuation_range(offset):
    provider, operation = "eia", "electricity_retail_sales"
    req = request(provider, operation, 1)
    value = encode_continuation(
        provider, operation, req.config, request_lineage(req, operation), {}
    )
    value["state"] = {"offset": offset}
    transport = MockProviderTransport([])
    result = await BreadthAdapter(
        provider, operation, RuntimeCredential("EIA_API_KEY", "synthetic")
    ).fetch(replace(req, continuation=value), transport)
    assert result.safe_errors and not result.safe_errors[0].retryable
    assert not transport.calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "page,found,expected_more,expected_coverage,next_page",
    [
        (999, 2000, True, False, 1000),
        (1000, 2000, False, False, None),
        (1000, 2001, False, True, None),
    ],
)
async def test_marketaux_terminal_page_policy(
    page, found, expected_more, expected_coverage, next_page
):
    req = request("marketaux", "news_all", 2)
    continuation = encode_continuation(
        "marketaux",
        "news_all",
        req.config,
        request_lineage(req, "news_all"),
        {"page": page},
    )
    result = await BreadthAdapter(
        "marketaux", "news_all", RuntimeCredential("MARKETAUX_API_TOKEN", "synthetic")
    ).fetch(
        replace(req, continuation=continuation),
        MockProviderTransport(
            [response({"data": [news(f"p{page}a"), news(f"p{page}b")], "meta": {"found": found}})]
        ),
    )
    assert not result.safe_errors
    assert result.has_more is expected_more
    assert result.coverage_incomplete is expected_coverage
    assert (result.continuation["state"]["page"] if result.continuation else None) == next_page


@pytest.mark.asyncio
async def test_marketaux_terminal_coverage_is_durable_partial_without_loop():
    engine = create_async_engine(DB)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ids = await seed(factory, "marketaux", "news_all", pages=2)
    repository = TargetRepository(factory, build_operation_registry())
    transport = MockProviderTransport(
        [response({"data": [news("terminal-a"), news("terminal-b")], "meta": {"found": 2001}})]
    )
    worker = CollectionControlPlaneWorker(
        factory,
        repository,
        RedisDouble(),
        transport,
        environ={"MARKETAUX_API_TOKEN": "synthetic"},
    )
    dispatch = TargetDispatch(ids[2], 1, 1, "normal", dispatch_identity(ids[2], 1, 1))
    try:
        loaded = await repository.load_for_execution(ids[2], 1)
        run_id, cursor = await worker._start_run(loaded, dispatch, CollectionRunMode.NORMAL)
        config, _ = await worker._resolved_run_config(
            run_id, loaded, cursor, CollectionRunMode.NORMAL
        )
        terminal = encode_continuation(
            "marketaux",
            "news_all",
            config,
            worker._lineage(loaded, CollectionRunMode.NORMAL),
            {"page": 1000},
        )
        async with factory.begin() as session:
            pending = await session.scalar(
                select(CollectionCursor)
                .where(CollectionCursor.target_id == ids[2])
                .with_for_update()
            )
            pending.continuation = terminal
        report = await worker.execute(dispatch)
        assert report.status == "partial" and report.safe_error == "coverage_incomplete"
        assert len(transport.calls) == 1
        async with factory() as session:
            run = await session.get(CollectionRun, report.run_id)
            cursor = await session.scalar(
                select(CollectionCursor).where(CollectionCursor.target_id == ids[2])
            )
            assert run.status.value == "partial" and run.error_code == "coverage_incomplete"
            assert cursor.continuation is None and cursor.continuation_run_id is None
    finally:
        await cleanup(factory, ids)
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["finnhub", "sec_recent", "sec_history"])
@pytest.mark.parametrize("conflict", [False, True])
async def test_local_keyset_deduplicates_before_limit(path, conflict):
    provider = "finnhub" if path == "finnhub" else "sec_edgar"
    operation = "company_news" if path == "finnhub" else "submissions_recent"
    body = (
        sec_history_duplicate_page(conflict=conflict)
        if path == "sec_history"
        else duplicate_page(provider, operation, conflict=conflict)
    )
    req = replace(operation_request(provider, operation, path), limit=1)
    adapter = BreadthAdapter(
        provider, operation, RuntimeCredential(CREDENTIALS[provider], "synthetic")
    )
    first = await adapter.fetch(req, MockProviderTransport([response(body)]))
    second = await adapter.fetch(req, MockProviderTransport([response(body)]))
    if conflict:
        for result in (first, second):
            assert result.safe_errors and not result.safe_errors[0].retryable
            assert not result.raw_items and result.continuation is None
    else:
        for result in (first, second):
            assert not result.safe_errors
            assert len(result.raw_items) == 1
            assert not result.has_more and result.continuation is None


@pytest.mark.asyncio
async def test_marketaux_page_and_failure_preserve_continuation():
    transport = MockProviderTransport(
        [response({"data": [news("a"), news("b")], "meta": {"found": 4}}), response({}, 500)]
    )
    adapter = BreadthAdapter(
        "marketaux", "news_all", RuntimeCredential("MARKETAUX_API_TOKEN", "synthetic")
    )
    first = await adapter.fetch(request("marketaux", "news_all"), transport)
    assert first.has_more and first.continuation["state"]["page"] == 2
    second = await adapter.fetch(
        replace(request("marketaux", "news_all"), continuation=first.continuation), transport
    )
    assert second.safe_errors and transport.calls[1].params["page"] == 2


@pytest.mark.parametrize(
    "field,value", [("forms", ["S-1"]), ("cik", "https://invalid"), ("max_history_files", 999)]
)
def test_sec_config_fail_closed(field, value):
    with pytest.raises(ValueError):
        breadth_config(
            "sec_edgar",
            "submissions_recent",
            {**CONFIGS["sec_edgar", "submissions_recent"], field: value},
        )


class RedisDouble:
    def __init__(self):
        self.values = {}

    async def set(self, key, value, **kwargs):
        if kwargs.get("nx") and key in self.values:
            return False
        self.values[key] = value
        return True

    async def get(self, key):
        return self.values.get(key)

    async def exists(self, key):
        return key in self.values

    async def eval(self, script, count, key, owner, *args):
        if self.values.get(key) != owner:
            return 0
        if "del" in script.lower():
            self.values.pop(key, None)
        return 1


async def seed(factory, provider, operation, pages=3):
    marker = uuid4().hex
    async with factory.begin() as session:
        source = await session.scalar(
            text(
                "INSERT INTO sources(code,name,source_type,access_method,authorization_status,retention_class,enabled) VALUES (:code,'M2 test','api',:provider,'authorized','metadata_only',true) RETURNING id"
            ),
            {"code": "m2-" + marker, "provider": provider},
        )
        account = await session.scalar(
            text(
                "INSERT INTO source_accounts(source_id,identity_status,enabled,collection_options) VALUES (:source,'verified',true,'{}') RETURNING id"
            ),
            {"source": source},
        )
        target = await session.scalar(
            text("""INSERT INTO collection_targets(target_key,source_id,source_account_id,operation_key,legacy_cursor_type,operation_config_version,provider_contract_version,operation_config,status,cadence_seconds,batch_limit,max_requests_per_run,max_pages_per_run,max_response_bytes,request_timeout_seconds,max_runtime_seconds,cursor_strategy,collection_mode,backfill_policy,revision_policy,rate_limit_group,next_due_at,health_status)
          VALUES (:key,:source,:account,:operation,NULL,2,2,CAST(:config AS jsonb),'active',300,2,:pages,:pages,1000000,10,60,'compound','incremental','disabled','ignore',:key,:now,'unknown') RETURNING id"""),
            {
                "key": "m2." + marker,
                "source": source,
                "account": account,
                "operation": operation,
                "config": json.dumps(CONFIGS[provider, operation]),
                "pages": pages,
                "now": datetime.now(UTC),
            },
        )
    return source, account, target


async def cleanup(factory, ids):
    source, _account, _target = ids
    async with factory.begin() as session:
        await session.execute(
            text(
                "DELETE FROM audit_logs WHERE target_id IN (SELECT id FROM collection_targets WHERE source_id=:source)"
            ),
            {"source": source},
        )
        await session.execute(
            text(
                "DELETE FROM evidence_projection_links WHERE safe_fact_projection_id IN (SELECT p.id FROM safe_fact_projections p JOIN raw_items r ON r.id=p.raw_item_id WHERE r.source_id=:source)"
            ),
            {"source": source},
        )
        await session.execute(
            text(
                "DELETE FROM notifications WHERE content_item_id IN (SELECT id FROM content_items WHERE source_id=:source)"
            ),
            {"source": source},
        )
        for table in ("evidence_items", "content_items"):
            await session.execute(
                text(f"DELETE FROM {table} WHERE source_id=:source"), {"source": source}
            )
        await session.execute(
            text(
                "DELETE FROM safe_fact_projections WHERE raw_item_id IN (SELECT id FROM raw_items WHERE source_id=:source)"
            ),
            {"source": source},
        )
        for table in ("raw_item_observations", "raw_items"):
            await session.execute(
                text(f"DELETE FROM {table} WHERE source_id=:source"), {"source": source}
            )
        await session.execute(
            text(
                "DELETE FROM collection_cursors WHERE target_id IN (SELECT id FROM collection_targets WHERE source_id=:source) OR source_account_id IN (SELECT id FROM source_accounts WHERE source_id=:source)"
            ),
            {"source": source},
        )
        await session.execute(
            text("DELETE FROM collection_runs WHERE source_id=:source"), {"source": source}
        )
        await session.execute(
            text("DELETE FROM collection_targets WHERE source_id=:source"), {"source": source}
        )
        await session.execute(
            text("DELETE FROM source_accounts WHERE source_id=:source"), {"source": source}
        )
        await session.execute(text("DELETE FROM sources WHERE id=:source"), {"source": source})


@pytest.mark.asyncio
@pytest.mark.parametrize("provider,operation", CONFIGS)
async def test_collection_to_durable_evidence(provider, operation):
    engine = create_async_engine(DB)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ids = await seed(factory, provider, operation)
    try:
        transport = MockProviderTransport([response(rows(provider, operation))])
        worker = CollectionControlPlaneWorker(
            factory,
            TargetRepository(factory, build_operation_registry()),
            RedisDouble(),
            transport,
            environ={CREDENTIALS[provider]: "synthetic", "SEC_CONTACT_EMAIL": "test@example.com"},
        )
        identity = dispatch_identity(ids[2], 1, 1, "normal")
        report = await worker.execute(TargetDispatch(ids[2], 1, 1, "normal", identity))
        assert report.status == "succeeded", report
        assert (await SafeFactProjectionWorker(factory).process_batch()).ready > 0
        assert (await EvidenceProjectionHandoffWorker(factory).process_batch()).linked > 0
        async with factory() as session:
            raw = tuple(await session.scalars(select(RawItem).where(RawItem.source_id == ids[0])))
            evidence = tuple(
                await session.scalars(select(EvidenceItem).where(EvidenceItem.source_id == ids[0]))
            )
            content = tuple(
                await session.scalars(select(ContentItem).where(ContentItem.source_id == ids[0]))
            )
            assert len(raw) == len(evidence)
            assert len(content) == (0 if provider == "eia" else len(raw))
            assert (
                all(e.provider_item_type == "finnhub_company_news" for e in evidence)
                if provider == "finnhub"
                else True
            )
    finally:
        await cleanup(factory, ids)
        await engine.dispose()


@pytest.mark.asyncio
async def test_finnhub_cross_symbol_identity_preserves_lineage_and_single_notification():
    engine = create_async_engine(DB)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ids = await seed(factory, "finnhub", "company_news")
    second_target = uuid4()
    try:
        async with factory.begin() as session:
            first = await session.get(CollectionTarget, ids[2])
            session.add(
                CollectionTarget(
                    id=second_target,
                    target_key=f"m2.{uuid4().hex}",
                    source_id=ids[0],
                    source_account_id=ids[1],
                    operation_key="company_news",
                    operation_config_version=2,
                    provider_contract_version=2,
                    operation_config={
                        **CONFIGS["finnhub", "company_news"],
                        "symbol": "MSFT",
                    },
                    status=first.status,
                    cadence_seconds=first.cadence_seconds,
                    batch_limit=first.batch_limit,
                    max_requests_per_run=first.max_requests_per_run,
                    max_pages_per_run=first.max_pages_per_run,
                    max_response_bytes=first.max_response_bytes,
                    request_timeout_seconds=first.request_timeout_seconds,
                    max_runtime_seconds=first.max_runtime_seconds,
                    cursor_strategy=first.cursor_strategy,
                    cursor_version=first.cursor_version,
                    collection_mode=first.collection_mode,
                    backfill_policy=first.backfill_policy,
                    revision_policy=first.revision_policy,
                    rate_limit_group=f"m2.{uuid4().hex}",
                    next_due_at=datetime.now(UTC),
                    health_status=first.health_status,
                )
            )
            await _persist_cutover_watermark(
                session,
                IntentWatermark(datetime(2000, 1, 1, tzinfo=UTC), uuid5(NAMESPACE_URL, "0")),
            )
        shared = [rows("finnhub", "company_news")[0]]
        transport = MockProviderTransport(
            [
                response(shared),
                response({}, 500),
                response(shared),
                response(empty_page("finnhub", "company_news")),
                response(empty_page("finnhub", "company_news")),
            ]
        )
        worker = CollectionControlPlaneWorker(
            factory,
            TargetRepository(factory, build_operation_registry()),
            RedisDouble(),
            transport,
            environ={"FINNHUB_API_KEY": "synthetic"},
        )
        first = TargetDispatch(ids[2], 1, 1, "normal", dispatch_identity(ids[2], 1, 1))
        assert (await worker.execute(first)).status == "succeeded"
        async with factory() as session:
            first_before_retry = await session.scalar(
                select(CollectionCursor).where(
                    CollectionCursor.target_id == ids[2],
                    CollectionCursor.run_mode == CollectionRunMode.NORMAL,
                )
            )
            first_snapshot = (
                first_before_retry.cursor_value,
                first_before_retry.watermark_at,
                first_before_retry.continuation,
            )
        second = TargetDispatch(
            second_target, 1, 1, "normal", dispatch_identity(second_target, 1, 1)
        )
        assert (await worker.execute(second)).status == "retry"
        async with factory() as session:
            first_during_retry = await session.scalar(
                select(CollectionCursor).where(
                    CollectionCursor.target_id == ids[2],
                    CollectionCursor.run_mode == CollectionRunMode.NORMAL,
                )
            )
            assert (
                first_during_retry.cursor_value,
                first_during_retry.watermark_at,
                first_during_retry.continuation,
            ) == first_snapshot
        assert (await worker.execute(second)).status == "succeeded"
        backfill = TargetDispatch(
            ids[2], 1, 2, "backfill", dispatch_identity(ids[2], 1, 2, "backfill")
        )
        assert (await worker.execute(backfill)).status == "succeeded"
        repeated = TargetDispatch(ids[2], 1, 3, "normal", dispatch_identity(ids[2], 1, 3))
        assert (await worker.execute(repeated)).status == "succeeded"
        assert (await SafeFactProjectionWorker(factory).process_batch()).ready == 2
        assert (await EvidenceProjectionHandoffWorker(factory).process_batch()).linked == 2
        await EvidenceProjectionHandoffWorker(factory).process_batch()
        first_reconcile = await NotificationIntentReconciler(factory).reconcile()
        second_reconcile = await NotificationIntentReconciler(factory).reconcile()
        async with factory() as session:
            assert (
                await session.scalar(
                    select(func.count()).select_from(RawItem).where(RawItem.source_id == ids[0])
                )
                == 1
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(RawItemObservation)
                    .where(RawItemObservation.source_id == ids[0])
                )
                == 2
            )
            projections = tuple(
                await session.scalars(
                    select(SafeFactProjection).join(RawItem).where(RawItem.source_id == ids[0])
                )
            )
            assert {row.factual_payload["symbol"] for row in projections} == {"NVDA", "MSFT"}
            cursors = tuple(
                await session.scalars(
                    select(CollectionCursor)
                    .where(CollectionCursor.target_id.in_((ids[2], second_target)))
                    .order_by(CollectionCursor.target_id, CollectionCursor.run_mode)
                )
            )
            assert len(cursors) == 3
            assert {(row.target_id, row.run_mode) for row in cursors} == {
                (ids[2], CollectionRunMode.NORMAL),
                (ids[2], CollectionRunMode.BACKFILL),
                (second_target, CollectionRunMode.NORMAL),
            }
            assert all(
                row.continuation is None and row.continuation_run_id is None for row in cursors
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(EvidenceItem)
                    .where(EvidenceItem.source_id == ids[0])
                )
                == 1
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(ContentItem)
                    .where(ContentItem.source_id == ids[0])
                )
                == 1
            )
            assert await session.scalar(select(func.count()).select_from(Notification)) == 1
        assert first_reconcile.created == 1 and second_reconcile.created == 0
    finally:
        async with factory.begin() as session:
            await session.execute(
                system_metadata.delete().where(system_metadata.c.key == WATERMARK_KEY)
            )
        await cleanup(factory, ids)
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True])
async def test_marketaux_multipage_observation_and_retry_checkpoint(failure):
    engine = create_async_engine(DB)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ids = await seed(factory, "marketaux", "news_all", pages=3)
    first = response({"data": [news("a"), news("b")], "meta": {"found": 4}})
    second = response({"data": [news("b"), news("c")], "meta": {"found": 4}})
    transport = MockProviderTransport(
        [first, response({}, 500), second] if failure else [first, second]
    )
    redis = RedisDouble()
    worker = CollectionControlPlaneWorker(
        factory,
        TargetRepository(factory, build_operation_registry()),
        redis,
        transport,
        environ={"MARKETAUX_API_TOKEN": "synthetic"},
    )
    dispatch = TargetDispatch(ids[2], 1, 1, "normal", dispatch_identity(ids[2], 1, 1, "normal"))
    try:
        result = await worker.execute(dispatch)
        if failure:
            assert result.status == "retry"
            async with factory() as session:
                cursor = await session.scalar(
                    select(CollectionCursor).where(CollectionCursor.target_id == ids[2])
                )
                assert cursor.continuation["state"]["page"] == 2
            result = await worker.execute(dispatch)
        assert result.status == "succeeded", result
        assert [c.params["page"] for c in transport.calls] == ([1, 2, 2] if failure else [1, 2])
        await SafeFactProjectionWorker(factory).process_batch()
        await EvidenceProjectionHandoffWorker(factory).process_batch()
        async with factory() as session:
            raws = tuple(await session.scalars(select(RawItem).where(RawItem.source_id == ids[0])))
            observations = tuple(
                await session.scalars(
                    select(RawItemObservation).where(RawItemObservation.source_id == ids[0])
                )
            )
            evidence = tuple(
                await session.scalars(select(EvidenceItem).where(EvidenceItem.source_id == ids[0]))
            )
            assert len(raws) == len(evidence) == 3
            assert len(observations) == 4
            run = await session.get(CollectionRun, result.run_id)
            assert run.request_count == (3 if failure else 2)
            assert run.page_count == 2
    finally:
        await cleanup(factory, ids)
        await engine.dispose()


@pytest.mark.asyncio
async def test_sec_recent_history_duplicate_keeps_canonical_and_lineage():
    engine = create_async_engine(DB)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ids = await seed(factory, "sec_edgar", "submissions_recent")
    recent = rows("sec_edgar", "submissions_recent")
    recent["filings"]["files"] = [
        {
            "name": "CIK0001045810-submissions-001.json",
            "filingFrom": "2026-01-01",
            "filingTo": "2026-01-03",
        }
    ]
    transport = MockProviderTransport([response(recent), response(recent["filings"]["recent"])])
    try:
        worker = CollectionControlPlaneWorker(
            factory,
            TargetRepository(factory, build_operation_registry()),
            RedisDouble(),
            transport,
            environ={"SEC_USER_AGENT": "synthetic", "SEC_CONTACT_EMAIL": "test@example.com"},
        )
        result = await worker.execute(
            TargetDispatch(ids[2], 1, 1, "normal", dispatch_identity(ids[2], 1, 1, "normal"))
        )
        assert result.status == "succeeded", result
        assert transport.calls[1].operation == "submissions_history"
        await SafeFactProjectionWorker(factory).process_batch()
        assert (await EvidenceProjectionHandoffWorker(factory).process_batch()).linked == 2
        async with factory() as session:
            evidence = tuple(
                await session.scalars(select(EvidenceItem).where(EvidenceItem.source_id == ids[0]))
            )
            observations = tuple(
                await session.scalars(
                    select(RawItemObservation).where(RawItemObservation.source_id == ids[0])
                )
            )
            assert len(evidence) == 1 and len(observations) == 2
    finally:
        await cleanup(factory, ids)
        await engine.dispose()


@pytest.mark.asyncio
async def test_sec_recent_null_file_keyset_is_database_accepted_and_completes():
    engine = create_async_engine(DB)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ids = await seed(factory, "sec_edgar", "submissions_recent", pages=3)
    body = rows("sec_edgar", "submissions_recent")
    recent = body["filings"]["recent"]
    recent["accessionNumber"] = [
        "0001045810-26-000001",
        "0001045810-26-000002",
        "0001045810-26-000003",
    ]
    recent["filingDate"] = ["2026-01-02"] * 3
    recent["form"] = ["8-K"] * 3
    recent["primaryDocument"] = ["a.htm", "b.htm", "c.htm"]
    transport = MockProviderTransport([response(body), response(body)])
    try:
        worker = CollectionControlPlaneWorker(
            factory,
            TargetRepository(factory, build_operation_registry()),
            RedisDouble(),
            transport,
            environ={"SEC_USER_AGENT": "synthetic", "SEC_CONTACT_EMAIL": "test@example.com"},
        )
        report = await worker.execute(
            TargetDispatch(ids[2], 1, 1, "normal", dispatch_identity(ids[2], 1, 1))
        )
        assert report.status == "succeeded", report
        assert len(transport.calls) == 2
        async with factory() as session:
            cursor = await session.scalar(
                select(CollectionCursor).where(CollectionCursor.target_id == ids[2])
            )
            assert cursor.continuation is None and cursor.continuation_run_id is None
    finally:
        await cleanup(factory, ids)
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "filename", ["https://evil.example/a.json", "../a.json", "CIK9999999999-submissions-001.json"]
)
async def test_sec_unsafe_history_reference_is_rejected(filename):
    body = rows("sec_edgar", "submissions_recent")
    body["filings"]["files"] = [
        {"name": filename, "filingFrom": "2026-01-01", "filingTo": "2026-01-03"}
    ]
    adapter = BreadthAdapter(
        "sec_edgar", "submissions_recent", RuntimeCredential("SEC_USER_AGENT", "synthetic")
    )
    result = await adapter.fetch(
        request("sec_edgar", "submissions_recent"), MockProviderTransport([response(body)])
    )
    assert (result.safe_errors or result.rejected_row_hashes) and not result.raw_items


@pytest.mark.asyncio
async def test_budget_exhaustion_durably_preserves_next_page():
    engine = create_async_engine(DB)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ids = await seed(factory, "marketaux", "news_all", pages=1)
    transport = MockProviderTransport(
        [response({"data": [news("a"), news("b")], "meta": {"found": 10}})]
    )
    try:
        worker = CollectionControlPlaneWorker(
            factory,
            TargetRepository(factory, build_operation_registry()),
            RedisDouble(),
            transport,
            environ={"MARKETAUX_API_TOKEN": "synthetic"},
        )
        report = await worker.execute(
            TargetDispatch(ids[2], 1, 1, "normal", dispatch_identity(ids[2], 1, 1, "normal"))
        )
        assert report.status == "partial" and len(transport.calls) == 1
        async with factory() as session:
            cursor = await session.scalar(
                select(CollectionCursor).where(CollectionCursor.target_id == ids[2])
            )
            assert cursor.continuation["state"]["page"] == 2
            run = await session.get(CollectionRun, report.run_id)
            assert run.request_count == run.page_count == 1
            assert run.error_code == "coverage_incomplete"
    finally:
        await cleanup(factory, ids)
        await engine.dispose()


@pytest.mark.parametrize(
    "provider,operation,version",
    [
        ("finnhub", "company_news", 1),
        ("eia", "electricity_rto_region_data", 99),
        ("sec_edgar", "arbitrary", 2),
    ],
)
def test_unknown_operation_version_fails_closed(provider, operation, version):
    with pytest.raises(LookupError):
        UnifiedAdapterFactory().build(provider, operation, None, version)


@pytest.mark.parametrize("limit,pages,requests", [(101, 2, 2), (1, 21, 1), (1, 1, 21)])
def test_operation_budget_caps(limit, pages, requests):
    registry = build_operation_registry()
    contract = registry.resolve("marketaux", "news_all", 2, 2)
    with pytest.raises(ValueError):
        registry.validate(
            contract,
            CONFIGS["marketaux", "news_all"],
            batch_limit=limit,
            max_pages=pages,
            max_requests=requests,
        )


@pytest.mark.asyncio
async def test_company_news_keyset_survives_revision():
    adapter = BreadthAdapter(
        "finnhub", "company_news", RuntimeCredential("FINNHUB_API_KEY", "synthetic")
    )
    body = rows("finnhub", "company_news")
    first = await adapter.fetch(
        request("finnhub", "company_news", 1), MockProviderTransport([response(body)])
    )
    assert first.has_more
    changed = [dict(r) for r in body]
    changed[0]["headline"] = "Changed synthetic news"
    second = await adapter.fetch(
        replace(request("finnhub", "company_news", 1), continuation=first.continuation),
        MockProviderTransport([response(changed)]),
    )
    assert not second.safe_errors and second.raw_items and not second.has_more


@pytest.mark.asyncio
async def test_rto_series_stability_and_offset():
    adapter = BreadthAdapter(
        "eia", "electricity_rto_region_data", RuntimeCredential("EIA_API_KEY", "synthetic")
    )
    first_row = rows("eia", "electricity_rto_region_data")["response"]["data"][0]
    later = {**first_row, "period": "2026-01-01T02"}
    transport = MockProviderTransport(
        [
            response({"response": {"data": [first_row], "total": 2}}),
            response({"response": {"data": [later], "total": 2}}),
        ]
    )
    first = await adapter.fetch(request("eia", "electricity_rto_region_data", 1), transport)
    second = await adapter.fetch(
        replace(request("eia", "electricity_rto_region_data", 1), continuation=first.continuation),
        transport,
    )
    assert transport.calls[1].params["offset"] == 1
    assert (
        first.factual_projections[0]["series_identity"]
        == second.factual_projections[0]["series_identity"]
    )
    assert (
        first.factual_projections[0]["provider_item_id"]
        != second.factual_projections[0]["provider_item_id"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [True, float("nan"), float("inf")])
async def test_rto_rejects_nonfinite_and_boolean_facts(value):
    body = rows("eia", "electricity_rto_region_data")
    body["response"]["data"][0]["value"] = value
    result = await BreadthAdapter(
        "eia", "electricity_rto_region_data", RuntimeCredential("EIA_API_KEY", "synthetic")
    ).fetch(request("eia", "electricity_rto_region_data"), MockProviderTransport([response(body)]))
    assert len(result.rejected_row_hashes) == 1
    assert len(result.raw_items) == 1  # unrelated valid series survives with a traceable rejection
    assert all(
        p["region"] != body["response"]["data"][0]["respondent"] for p in result.factual_projections
    )


@pytest.mark.asyncio
async def test_0009_roundtrip_and_incompatible_target_guard():
    engine = create_async_engine(DB)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    revision = ScriptDirectory.from_config(Config("alembic.ini")).get_revision("0009")
    assert revision.down_revision == "0008"

    def roundtrip(connection):
        with Operations.context(MigrationContext.configure(connection)):
            revision.module.downgrade()
            revision.module.upgrade()

    async with engine.connect() as connection:
        transaction = await connection.begin()
        await connection.run_sync(roundtrip)
        await transaction.rollback()
    ids = await seed(factory, "finnhub", "company_news")
    try:

        def rejected(connection):
            with (
                Operations.context(MigrationContext.configure(connection)),
                pytest.raises(RuntimeError, match="migration_0009_incompatible_operation_state"),
            ):
                revision.module.downgrade()

        async with engine.begin() as connection:
            await connection.run_sync(rejected)
    finally:
        await cleanup(factory, ids)
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["append", "earlier", "revision", "reorder"])
async def test_company_news_keyset_mutation_and_overlap(change):
    adapter = BreadthAdapter(
        "finnhub", "company_news", RuntimeCredential("FINNHUB_API_KEY", "synthetic")
    )
    body = rows("finnhub", "company_news")
    first = await adapter.fetch(
        request("finnhub", "company_news", 1), MockProviderTransport([response(body)])
    )
    changed = [dict(r) for r in body]
    if change == "append":
        changed.append({**body[-1], "id": 99})
    elif change == "earlier":
        changed.insert(0, {**body[0], "id": 0, "datetime": body[0]["datetime"] - 60})
    elif change == "revision":
        changed[0]["headline"] = "Revised synthetic"
    else:
        changed.reverse()
    continuation = first.continuation
    emitted = []
    for _ in range(5):
        result = await adapter.fetch(
            replace(request("finnhub", "company_news", 1), continuation=continuation),
            MockProviderTransport([response(changed)]),
        )
        assert not result.safe_errors
        emitted.extend(p["provider_item_id"] for p in result.factual_projections)
        if not result.has_more:
            break
        continuation = result.continuation
    else:
        pytest.fail("bounded keyset did not complete")
    assert first.factual_projections[0]["provider_item_id"] not in emitted
    overlap = await adapter.fetch(
        request("finnhub", "company_news", 10), MockProviderTransport([response(changed)])
    )
    assert len(overlap.raw_items) == len(changed)
    assert not overlap.safe_errors


@pytest.mark.asyncio
async def test_finnhub_fallback_requires_url_and_is_collision_safe():
    adapter = BreadthAdapter(
        "finnhub", "company_news", RuntimeCredential("FINNHUB_API_KEY", "synthetic")
    )
    body = rows("finnhub", "company_news")
    for row in body:
        row.pop("id")
    result = await adapter.fetch(
        request("finnhub", "company_news"), MockProviderTransport([response(body)])
    )
    assert len({p["provider_item_id"] for p in result.factual_projections}) == 2
    for row in body:
        row.pop("url")
    invalid = await adapter.fetch(
        request("finnhub", "company_news"), MockProviderTransport([response(body)])
    )
    assert invalid.safe_errors and not invalid.raw_items

    variants = [
        {
            **rows("finnhub", "company_news")[0],
            "id": None,
            "url": "HTTPS://EXAMPLE.COM:443/story?utm_source=x&fbclid=y#fragment",
        },
        {
            **rows("finnhub", "company_news")[0],
            "id": None,
            "url": "https://example.com/story",
        },
    ]
    normalized = await adapter.fetch(
        request("finnhub", "company_news"), MockProviderTransport([response(variants)])
    )
    assert len({p["provider_item_id"] for p in normalized.factual_projections}) == 1
    variants[1]["url"] = "https://example.com/other-story"
    distinct = await adapter.fetch(
        request("finnhub", "company_news"), MockProviderTransport([response(variants)])
    )
    assert len({p["provider_item_id"] for p in distinct.factual_projections}) == 2


@pytest.mark.asyncio
async def test_v2_legacy_identity_load_revise_rejected():
    engine = create_async_engine(DB)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ids = await seed(factory, "marketaux", "news_all")
    try:
        async with factory.begin() as session:
            # Test tampered persisted identity without changing the production immutability trigger.
            await session.execute(
                text("ALTER TABLE collection_targets DISABLE TRIGGER trg_r1_target_identity_guard")
            )
            await session.execute(
                text(
                    "UPDATE collection_targets SET legacy_cursor_type='provider_cursor_v1' WHERE id=:id"
                ),
                {"id": ids[2]},
            )
            await session.execute(
                text("ALTER TABLE collection_targets ENABLE TRIGGER trg_r1_target_identity_guard")
            )
        repo = TargetRepository(factory, build_operation_registry())
        with pytest.raises(TargetRepositoryError, match="legacy_identity_mismatch"):
            await repo.load_for_execution(ids[2], 1)
        with pytest.raises(TargetRepositoryError, match="legacy_identity_mismatch"):
            await repo.revise(ids[2], 1, {"cadence_seconds": 600})
        assert (
            await ControlPlaneAuditService(factory, build_operation_registry()).shadow()
        ).config_mismatches >= 1
    finally:
        await cleanup(factory, ids)
        await engine.dispose()


@pytest.mark.asyncio
async def test_mixed_page_audit_is_durable_idempotent_and_no_legacy_cursor():
    engine = create_async_engine(DB)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ids = await seed(factory, "marketaux", "news_all")
    bad = {**news("bad-stable-id"), "published_at": "invalid-date"}
    transport = MockProviderTransport(
        [response({"data": [bad, news("valid-id")], "meta": {"found": 2}})] * 2
    )
    worker = CollectionControlPlaneWorker(
        factory,
        TargetRepository(factory, build_operation_registry()),
        RedisDouble(),
        transport,
        environ={"MARKETAUX_API_TOKEN": "synthetic"},
    )
    try:
        for slot in (1, 2):
            report = await worker.execute(
                TargetDispatch(
                    ids[2], 1, slot, "normal", dispatch_identity(ids[2], 1, slot, "normal")
                )
            )
            assert report.status == "succeeded", report
        async with factory() as session:
            raw = tuple(await session.scalars(select(RawItem).where(RawItem.source_id == ids[0])))
            assert len(raw) == 1
            audits = tuple(
                await session.scalars(
                    select(AuditLog).where(
                        AuditLog.target_id == ids[2], AuditLog.action == "provider_row_rejected"
                    )
                )
            )
            assert len(audits) == 1
            assert "bad-stable-id" not in json.dumps(audits[0].after)
            assert not tuple(
                await session.scalars(
                    select(CollectionCursor).where(
                        CollectionCursor.source_account_id == ids[1],
                        CollectionCursor.target_id.is_(None),
                    )
                )
            )
    finally:
        await cleanup(factory, ids)
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider,operation,history",
    [
        ("marketaux", "news_all", False),
        ("finnhub", "company_news", False),
        ("eia", "electricity_retail_sales", False),
        ("eia", "electricity_rto_region_data", False),
        ("sec_edgar", "submissions_recent", False),
        ("sec_edgar", "submissions_recent", True),
    ],
)
async def test_all_operation_paths_isolate_traceable_invalid_rows(provider, operation, history):
    adapter = BreadthAdapter(
        provider, operation, RuntimeCredential(CREDENTIALS[provider], "synthetic")
    )
    req = request(provider, operation, 2)
    if provider == "marketaux":
        body = {
            "data": [{**news("bad"), "published_at": "invalid"}, news("good")],
            "meta": {"found": 2},
        }
    elif provider == "finnhub":
        valid = rows(provider, operation)[0]
        body = [{**valid, "id": "invalid/id", "url": "https://example.com/rejected"}, valid]
    elif provider == "eia":
        valid = rows(provider, operation)["response"]["data"][0]
        value_field = "price" if operation == "electricity_retail_sales" else "value"
        body = {"response": {"data": [{**valid, value_field: True}, valid], "total": 2}}
    else:
        valid = {
            "accessionNumber": "0001045810-26-000002",
            "filingDate": "2026-01-02",
            "form": "8-K",
            "primaryDocument": "report.htm",
        }
        bad = {
            **valid,
            "accessionNumber": "0001045810-26-000001",
            "primaryDocument": "../blocked.htm",
        }
        columns = {key: [bad[key], valid[key]] for key in valid}
        body = columns if history else {"filings": {"recent": columns, "files": []}}
        if history:
            req = replace(
                req,
                continuation=encode_continuation(
                    provider,
                    operation,
                    req.config,
                    request_lineage(req, operation),
                    {"file": "CIK0001045810-submissions-001.json", "files": []},
                ),
            )
    result = await adapter.fetch(req, MockProviderTransport([response(body)]))
    assert not result.safe_errors
    assert len(result.raw_items) == len(result.factual_projections) == 1
    assert len(result.rejected_row_hashes) == 1
    assert len(result.rejected_row_hashes[0]) == 64


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reference",
    [
        {"name": "CIK0001045810-submissions-001.json", "filingTo": "2026-01-03"},
        {
            "name": "CIK0001045810-submissions-001.json",
            "filingFrom": "invalid",
            "filingTo": "2026-01-03",
        },
        {
            "name": "CIK0001045810-submissions-001.json",
            "filingFrom": "2026-01-03",
            "filingTo": "2026-01-01",
        },
    ],
)
async def test_sec_history_reference_metadata_fail_closed(reference):
    body = rows("sec_edgar", "submissions_recent")
    body["filings"]["files"] = [reference]
    result = await BreadthAdapter(
        "sec_edgar", "submissions_recent", RuntimeCredential("SEC_USER_AGENT", "synthetic")
    ).fetch(
        request("sec_edgar", "submissions_recent"),
        MockProviderTransport([response(body)]),
    )
    assert result.safe_errors and not result.raw_items and not result.rejected_row_hashes


@pytest.mark.parametrize(
    "key,value",
    [
        ("lookback_seconds", 32 * 86400),
        ("overlap_seconds", 3 * 86400),
        ("ingestion_lag_seconds", -1),
        ("granularity", "minute"),
    ],
)
def test_rolling_window_ceiling(key, value):
    with pytest.raises(ValueError):
        breadth_config("marketaux", "news_all", {**rolling_config(), key: value})


def test_fixed_and_rolling_window_resolution():
    assert resolve_window("news_all", CONFIGS["marketaux", "news_all"], NOW) == {
        "start": "2026-01-01",
        "end": "2026-01-03",
    }
    a = resolve_window("news_all", rolling_config(), NOW)
    b = resolve_window(
        "news_all", rolling_config(), NOW + timedelta(days=1), NOW - timedelta(days=1)
    )
    assert b["end"] > a["end"] and b["start"] <= a["end"]
    monthly = {
        k: v
        for k, v in CONFIGS["eia", "electricity_retail_sales"].items()
        if k not in {"start", "end"}
    }
    monthly.update(
        window_mode="rolling_window",
        lookback_months=1,
        overlap_months=0,
        ingestion_lag_months=0,
        granularity="month",
    )
    assert resolve_window("electricity_retail_sales", monthly, NOW) == {
        "start": "2025-12-01",
        "end": "2025-12-01",
    }


@pytest.mark.asyncio
async def test_rolling_runs_freeze_retry_and_overlap_revision(monkeypatch):
    import market_intelligence.collection.control_plane as cp

    class Clock(datetime):
        current = NOW

        @classmethod
        def now(cls, tz=None):
            return cls.current

    monkeypatch.setattr(cp, "datetime", Clock)
    engine = create_async_engine(DB)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ids = await seed(factory, "marketaux", "news_all")
    try:
        async with factory.begin() as session:
            target = await session.get(CollectionTarget, ids[2])
            target.operation_config = rolling_config()
            target.config_revision += 1
        a = news("stable-a")
        b = {**news("stable-b"), "published_at": "2026-01-03T12:00:00+00:00"}
        transport = MockProviderTransport(
            [
                response({"data": [a], "meta": {"found": 1}}),
                response({"data": [{**a, "title": "Revised synthetic"}, b], "meta": {"found": 2}}),
            ]
        )
        worker = CollectionControlPlaneWorker(
            factory,
            TargetRepository(factory, build_operation_registry()),
            RedisDouble(),
            transport,
            environ={"MARKETAUX_API_TOKEN": "synthetic"},
        )
        for slot in (1, 2):
            Clock.current = NOW + timedelta(days=slot - 1)
            report = await worker.execute(
                TargetDispatch(
                    ids[2], 2, slot, "normal", dispatch_identity(ids[2], 2, slot, "normal")
                )
            )
            assert report.status == "succeeded", report
        assert transport.calls[0].params["published_before"] == "2026-01-03"
        assert transport.calls[1].params["published_before"] == "2026-01-04"
        async with factory() as session:
            runs = tuple(
                await session.scalars(
                    select(CollectionRun)
                    .where(CollectionRun.target_id == ids[2])
                    .order_by(CollectionRun.started_at)
                )
            )
            assert runs[0].resolved_window != runs[1].resolved_window
            assert (
                len(
                    tuple(await session.scalars(select(RawItem).where(RawItem.source_id == ids[0])))
                )
                == 2
            )
            assert (
                len(
                    tuple(
                        await session.scalars(
                            select(RawItemObservation).where(RawItemObservation.source_id == ids[0])
                        )
                    )
                )
                == 3
            )
            assert (
                len(
                    tuple(
                        await session.scalars(
                            select(SafeFactProjection)
                            .join(RawItem)
                            .where(RawItem.source_id == ids[0])
                        )
                    )
                )
                == 3
            )
        assert runs[0].resolved_window["end"] == "2026-01-03"
        with pytest.raises(DBAPIError, match="collection_run_window_immutable"):
            async with factory.begin() as session:
                await session.execute(
                    text(
                        "UPDATE collection_runs SET resolved_window=jsonb_build_object('start','2026-01-02','end','2026-01-04') WHERE id=:id"
                    ),
                    {"id": runs[0].id},
                )
    finally:
        await cleanup(factory, ids)
        await engine.dispose()


@pytest.mark.asyncio
async def test_fixed_window_not_automatically_dispatched():
    engine = create_async_engine(DB)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ids = await seed(factory, "marketaux", "news_all")
    try:
        scheduler = TargetScheduler(
            TargetRepository(factory, build_operation_registry()), RedisDouble()
        )
        assert not await scheduler.claim_due(datetime.now(UTC) + timedelta(seconds=1))
        async with factory.begin() as session:
            target = await session.get(CollectionTarget, ids[2])
            target.operation_config = rolling_config()
            target.config_revision += 1
        assert len(await scheduler.claim_due(datetime.now(UTC) + timedelta(seconds=1))) == 1
    finally:
        await cleanup(factory, ids)
        await engine.dispose()


@pytest.mark.asyncio
async def test_many_fixed_targets_do_not_starve_rolling_due_target():
    engine = create_async_engine(DB)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    groups = [await seed(factory, "marketaux", "news_all") for _ in range(7)]
    rolling = groups[-1]
    try:
        async with factory.begin() as session:
            target = await session.get(CollectionTarget, rolling[2])
            target.operation_config = rolling_config()
            target.config_revision = 2
        claimed = await TargetScheduler(
            TargetRepository(factory, build_operation_registry()), RedisDouble()
        ).claim_due(datetime.now(UTC) + timedelta(seconds=1), limit=1)
        assert len(claimed) == 1 and claimed[0].target_id == rolling[2]
    finally:
        for ids in groups:
            await cleanup(factory, ids)
        await engine.dispose()


@pytest.mark.asyncio
async def test_pre_request_window_recovery_survives_new_run_and_time_boundary(monkeypatch):
    import market_intelligence.collection.control_plane as cp

    class Clock(datetime):
        current = NOW.replace(hour=23, minute=59, second=30)

        @classmethod
        def now(cls, tz=None):
            return cls.current

    monkeypatch.setattr(cp, "datetime", Clock)
    engine = create_async_engine(DB)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ids = await seed(factory, "marketaux", "news_all")
    try:
        async with factory.begin() as session:
            target = await session.get(CollectionTarget, ids[2])
            target.operation_config = rolling_config()
            target.config_revision = 2
        repository = TargetRepository(factory, build_operation_registry())
        transport = MockProviderTransport([response({"data": [], "meta": {"found": 0}})] * 2)
        worker = CollectionControlPlaneWorker(
            factory,
            repository,
            RedisDouble(),
            transport,
            environ={"MARKETAUX_API_TOKEN": "synthetic"},
        )
        loaded = await repository.load_for_execution(ids[2], 2)
        first = TargetDispatch(ids[2], 2, 1, "normal", dispatch_identity(ids[2], 2, 1))
        run_id, cursor = await worker._start_run(loaded, first, CollectionRunMode.NORMAL)
        config, continuation = await worker._resolved_run_config(
            run_id, loaded, cursor, CollectionRunMode.NORMAL
        )
        assert continuation["resolved_window"] == {
            "start": config["start"],
            "end": config["end"],
        }
        async with factory.begin() as session:
            run = await session.get(CollectionRun, run_id)
            run.status = cp.CollectionRunStatus.FAILED
            run.finished_at = Clock.current
        Clock.current += timedelta(days=2)
        second = TargetDispatch(ids[2], 2, 2, "normal", dispatch_identity(ids[2], 2, 2))
        result = await worker.execute(second)
        assert result.status == "succeeded"
        call = transport.calls[0]
        assert call.params["published_after"] == config["start"]
        assert call.params["published_before"] == config["end"]
        async with factory() as session:
            persisted = await session.scalar(
                select(CollectionCursor).where(CollectionCursor.target_id == ids[2])
            )
            assert persisted.continuation is None
            assert persisted.continuation_run_id is None
        Clock.current += timedelta(days=1)
        third = TargetDispatch(ids[2], 2, 3, "normal", dispatch_identity(ids[2], 2, 3))
        assert (await worker.execute(third)).status == "succeeded"
        assert transport.calls[1].params["published_before"] != config["end"]
    finally:
        await cleanup(factory, ids)
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider,operation", CONFIGS)
async def test_v2_empty_completion_clears_pending_continuation(provider, operation, monkeypatch):
    import market_intelligence.collection.control_plane as cp

    class Clock(datetime):
        current = NOW

        @classmethod
        def now(cls, tz=None):
            return cls.current

    monkeypatch.setattr(cp, "datetime", Clock)
    engine = create_async_engine(DB)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ids = await seed(factory, provider, operation)
    try:
        async with factory.begin() as session:
            target = await session.get(CollectionTarget, ids[2])
            target.operation_config = rolling_operation_config(provider, operation)
            target.config_revision = 2
        transport = MockProviderTransport(
            [response(empty_page(provider, operation)), response(empty_page(provider, operation))]
        )
        worker = CollectionControlPlaneWorker(
            factory,
            TargetRepository(factory, build_operation_registry()),
            RedisDouble(),
            transport,
            environ={CREDENTIALS[provider]: "synthetic", "SEC_CONTACT_EMAIL": "test@example.com"},
        )
        for slot in (1, 2):
            report = await worker.execute(
                TargetDispatch(ids[2], 2, slot, "normal", dispatch_identity(ids[2], 2, slot))
            )
            assert report.status == "succeeded"
            async with factory() as session:
                cursor = await session.scalar(
                    select(CollectionCursor).where(CollectionCursor.target_id == ids[2])
                )
                assert cursor.continuation is None
                assert cursor.continuation_run_id is None
            Clock.current += timedelta(days=32 if operation == "electricity_retail_sales" else 1)
        async with factory() as session:
            run_windows = tuple(
                await session.scalars(
                    select(CollectionRun.resolved_window)
                    .where(CollectionRun.target_id == ids[2])
                    .order_by(CollectionRun.started_at)
                )
            )
            assert run_windows[0] != run_windows[1]
    finally:
        await cleanup(factory, ids)
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation,error",
    [
        ("cursor_type='company_news'", "collection_cursor_target_identity_invalid"),
        ("cursor_version=2", "collection_cursor_target_identity_invalid"),
        ("source_account_id=:other_account", "collection_cursor_target_identity_invalid"),
        ("run_mode='backfill'", "collection_continuation_run_invalid"),
        (
            "continuation=jsonb_set(continuation,'{lineage,config_revision}','999'::jsonb)",
            "collection_continuation_contract_invalid",
        ),
        (
            "continuation=jsonb_set(continuation,'{lineage,operation_config_version}','1'::jsonb)",
            "collection_continuation_contract_invalid",
        ),
        (
            "continuation=jsonb_set(continuation,'{lineage,provider_contract_version}','1'::jsonb)",
            "collection_continuation_contract_invalid",
        ),
        (
            "continuation=jsonb_set(continuation,'{resolved_window,start}','\"2025-12-01\"'::jsonb)",
            "collection_continuation_contract_invalid",
        ),
        (
            "continuation=jsonb_set(continuation,'{config_hash}',to_jsonb(repeat('f',64)))",
            "collection_continuation_contract_invalid",
        ),
        (
            "continuation=jsonb_set(continuation,'{state}',jsonb_build_object('offset',1))",
            "collection_continuation_state_invalid",
        ),
    ],
)
async def test_database_continuation_lineage_guard_rejects_bypass(mutation, error):
    engine = create_async_engine(DB)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ids = await seed(factory, "marketaux", "news_all")
    try:
        async with factory.begin() as session:
            target = await session.get(CollectionTarget, ids[2])
            target.operation_config = rolling_config()
            target.config_revision = 2
            other_account = await session.scalar(
                text(
                    "INSERT INTO source_accounts(source_id,identity_status,enabled,collection_options) VALUES (:source,'verified',true,'{}') RETURNING id"
                ),
                {"source": ids[0]},
            )
        repository = TargetRepository(factory, build_operation_registry())
        worker = CollectionControlPlaneWorker(
            factory,
            repository,
            RedisDouble(),
            MockProviderTransport([]),
            environ={"MARKETAUX_API_TOKEN": "synthetic"},
        )
        loaded = await repository.load_for_execution(ids[2], 2)
        dispatch = TargetDispatch(ids[2], 2, 1, "normal", dispatch_identity(ids[2], 2, 1))
        run_id, cursor = await worker._start_run(loaded, dispatch, CollectionRunMode.NORMAL)
        await worker._resolved_run_config(run_id, loaded, cursor, CollectionRunMode.NORMAL)
        with pytest.raises(DBAPIError, match=error):
            async with factory.begin() as session:
                await session.execute(
                    text(f"UPDATE collection_cursors SET {mutation} WHERE target_id=:target"),
                    {"target": ids[2], "other_account": other_account},
                )
        async with factory.begin() as session:
            await session.execute(
                text("DELETE FROM source_accounts WHERE id=:id"), {"id": other_account}
            )
    finally:
        await cleanup(factory, ids)
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider,operation,state",
    [
        ("marketaux", "news_all", {"page": True}),
        ("marketaux", "news_all", {"page": "2"}),
        ("marketaux", "news_all", {"page": 1}),
        ("marketaux", "news_all", {"page": 1001}),
        ("finnhub", "company_news", {"last_key": ["only-one"]}),
        ("finnhub", "company_news", {"last_key": ["", "identity"]}),
        ("eia", "electricity_retail_sales", {"offset": True}),
        ("eia", "electricity_rto_region_data", {"offset": "1"}),
        ("eia", "electricity_retail_sales", {"offset": 0}),
        ("eia", "electricity_rto_region_data", {"offset": 10_000_001}),
        (
            "sec_edgar",
            "submissions_recent",
            {"file": "CIK0000320193-submissions-001.json"},
        ),
        (
            "sec_edgar",
            "submissions_recent",
            {"files": ["CIK0001045810-submissions-001.json"]},
        ),
        (
            "sec_edgar",
            "submissions_recent",
            {
                "file": "CIK0001045810-submissions-001.json",
                "files": ["CIK0001045810-submissions-001.json"],
            },
        ),
        ("sec_edgar", "submissions_recent", {"last_key": ["only-one"]}),
        ("sec_edgar", "submissions_recent", {"offset": 1}),
    ],
)
async def test_database_continuation_state_values_fail_closed(provider, operation, state):
    engine = create_async_engine(DB)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ids = await seed(factory, provider, operation)
    try:
        async with factory.begin() as session:
            target = await session.get(CollectionTarget, ids[2])
            target.operation_config = rolling_operation_config(provider, operation)
            target.config_revision = 2
        repository = TargetRepository(factory, build_operation_registry())
        worker = CollectionControlPlaneWorker(
            factory,
            repository,
            RedisDouble(),
            MockProviderTransport([]),
            environ={CREDENTIALS[provider]: "synthetic", "SEC_CONTACT_EMAIL": "test@example.com"},
        )
        loaded = await repository.load_for_execution(ids[2], 2)
        dispatch = TargetDispatch(ids[2], 2, 1, "normal", dispatch_identity(ids[2], 2, 1))
        run_id, cursor = await worker._start_run(loaded, dispatch, CollectionRunMode.NORMAL)
        await worker._resolved_run_config(run_id, loaded, cursor, CollectionRunMode.NORMAL)
        with pytest.raises(DBAPIError, match="collection_continuation_state_invalid"):
            async with factory.begin() as session:
                await session.execute(
                    text("""UPDATE collection_cursors
                      SET continuation=jsonb_set(continuation,'{state}',CAST(:state AS jsonb))
                      WHERE target_id=:target"""),
                    {"state": json.dumps(state), "target": ids[2]},
                )
    finally:
        await cleanup(factory, ids)
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("offset", [99_999, 100_000, 100_001, 9_999_999, 10_000_000])
async def test_database_eia_offset_policy_accepts_same_boundaries_as_codec(offset):
    provider, operation = "eia", "electricity_retail_sales"
    engine = create_async_engine(DB)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ids = await seed(factory, provider, operation)
    try:
        async with factory.begin() as session:
            target = await session.get(CollectionTarget, ids[2])
            target.operation_config = rolling_operation_config(provider, operation)
            target.config_revision = 2
        repository = TargetRepository(factory, build_operation_registry())
        worker = CollectionControlPlaneWorker(
            factory,
            repository,
            RedisDouble(),
            MockProviderTransport([]),
            environ={"EIA_API_KEY": "synthetic"},
        )
        loaded = await repository.load_for_execution(ids[2], 2)
        dispatch = TargetDispatch(ids[2], 2, 1, "normal", dispatch_identity(ids[2], 2, 1))
        run_id, cursor = await worker._start_run(loaded, dispatch, CollectionRunMode.NORMAL)
        await worker._resolved_run_config(run_id, loaded, cursor, CollectionRunMode.NORMAL)
        async with factory.begin() as session:
            await session.execute(
                text("""UPDATE collection_cursors
                  SET continuation=jsonb_set(continuation,'{state}',CAST(:state AS jsonb))
                  WHERE target_id=:target"""),
                {"state": json.dumps({"offset": offset}), "target": ids[2]},
            )
    finally:
        await cleanup(factory, ids)
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state,accepted",
    [
        ({"file": "CIK0001045810-submissions-001.json"}, True),
        ({"file": "CIK0001045810-submissions-001.json", "files": []}, True),
        (
            {
                "file": "CIK0001045810-submissions-001.json",
                "files": ["CIK0001045810-submissions-002.json"],
            },
            True,
        ),
        (
            {"file": None, "files": [], "last_key": ["2026-01-02", "0001045810-26-000001"]},
            True,
        ),
        (
            {
                "file": "CIK0001045810-submissions-001.json",
                "files": [],
                "last_key": ["2026-01-02", "0001045810-26-000001"],
            },
            True,
        ),
        ({"file": None}, False),
        ({"file": None, "files": []}, False),
        (
            {"file": None, "files": ["CIK0001045810-submissions-001.json"]},
            False,
        ),
        (
            {
                "file": "CIK0001045810-submissions-001.json",
                "last_key": ["2026-01-02", "0001045810-26-000001"],
            },
            False,
        ),
        ({"file": "CIK0000320193-submissions-001.json"}, False),
        (
            {
                "file": "CIK0001045810-submissions-001.json",
                "files": ["CIK0001045810-submissions-001.json"],
            },
            False,
        ),
        (
            {
                "file": "CIK0001045810-submissions-001.json",
                "files": ["CIK0001045810-submissions-002.json"] * 2,
            },
            False,
        ),
        (
            {
                "file": "CIK0001045810-submissions-001.json",
                "files": [f"CIK0001045810-submissions-{i:03d}.json" for i in range(2, 8)],
            },
            False,
        ),
        ({"file": "CIK0001045810-submissions-001.json", "unknown": 1}, False),
    ],
)
async def test_sec_continuation_python_and_postgres_acceptance_are_symmetric(state, accepted):
    provider, operation = "sec_edgar", "submissions_recent"
    req = request(provider, operation)
    lineage = request_lineage(req, operation)
    encoded = encode_continuation(provider, operation, req.config, lineage, {})
    encoded["state"] = state
    if accepted:
        assert decode_continuation(encoded, provider, operation, req.config, lineage) == state
    else:
        with pytest.raises(ContinuationContractError):
            decode_continuation(encoded, provider, operation, req.config, lineage)

    engine = create_async_engine(DB)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ids = await seed(factory, provider, operation)
    try:
        async with factory.begin() as session:
            target = await session.get(CollectionTarget, ids[2])
            target.operation_config = rolling_operation_config(provider, operation)
            target.config_revision = 2
        repository = TargetRepository(factory, build_operation_registry())
        worker = CollectionControlPlaneWorker(
            factory,
            repository,
            RedisDouble(),
            MockProviderTransport([]),
            environ={"SEC_USER_AGENT": "synthetic", "SEC_CONTACT_EMAIL": "test@example.com"},
        )
        loaded = await repository.load_for_execution(ids[2], 2)
        dispatch = TargetDispatch(ids[2], 2, 1, "normal", dispatch_identity(ids[2], 2, 1))
        run_id, cursor = await worker._start_run(loaded, dispatch, CollectionRunMode.NORMAL)
        await worker._resolved_run_config(run_id, loaded, cursor, CollectionRunMode.NORMAL)
        if accepted:
            async with factory.begin() as session:
                await session.execute(
                    text("""UPDATE collection_cursors
                      SET continuation=jsonb_set(continuation,'{state}',CAST(:state AS jsonb))
                      WHERE target_id=:target"""),
                    {"state": json.dumps(state), "target": ids[2]},
                )
        else:
            with pytest.raises(DBAPIError, match="collection_continuation_state_invalid"):
                async with factory.begin() as session:
                    await session.execute(
                        text("""UPDATE collection_cursors
                          SET continuation=jsonb_set(continuation,'{state}',CAST(:state AS jsonb))
                          WHERE target_id=:target"""),
                        {"state": json.dumps(state), "target": ids[2]},
                    )
    finally:
        await cleanup(factory, ids)
        await engine.dispose()


@pytest.mark.asyncio
async def test_database_run_lifecycle_guards_and_recovery_states():
    engine = create_async_engine(DB)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ids = await seed(factory, "marketaux", "news_all")
    try:
        async with factory.begin() as session:
            target = await session.get(CollectionTarget, ids[2])
            target.operation_config = rolling_config()
            target.config_revision = 2
        repository = TargetRepository(factory, build_operation_registry())
        worker = CollectionControlPlaneWorker(
            factory,
            repository,
            RedisDouble(),
            MockProviderTransport([]),
            environ={"MARKETAUX_API_TOKEN": "synthetic"},
        )
        loaded = await repository.load_for_execution(ids[2], 2)
        dispatch = TargetDispatch(ids[2], 2, 1, "normal", dispatch_identity(ids[2], 2, 1))
        run_id, cursor = await worker._start_run(loaded, dispatch, CollectionRunMode.NORMAL)
        await worker._resolved_run_config(run_id, loaded, cursor, CollectionRunMode.NORMAL)

        for status in ("partial", "failed"):
            async with factory.begin() as session:
                await session.execute(
                    text("UPDATE collection_runs SET status=:status WHERE id=:run"),
                    {"status": status, "run": run_id},
                )
        with pytest.raises(DBAPIError, match="collection_succeeded_run_has_continuation"):
            async with factory.begin() as session:
                await session.execute(
                    text("UPDATE collection_runs SET status='succeeded' WHERE id=:run"),
                    {"run": run_id},
                )

        late_run = uuid4()
        async with factory.begin() as session:
            session.add(
                CollectionRun(
                    id=late_run,
                    source_id=ids[0],
                    source_account_id=ids[1],
                    target_id=ids[2],
                    run_mode=CollectionRunMode.BACKFILL,
                    dispatch_identity=f"late-freeze-{late_run}",
                    started_at=datetime.now(UTC),
                    status="running",
                    request_count=1,
                )
            )
        with pytest.raises(DBAPIError, match="collection_run_freeze_too_late"):
            async with factory.begin() as session:
                await session.execute(
                    text("""UPDATE collection_runs SET
                      resolved_window=CAST(:window AS jsonb), operation_config_hash=:digest
                      WHERE id=:run"""),
                    {
                        "window": json.dumps({"start": "2026-01-01", "end": "2026-01-03"}),
                        "digest": "a" * 64,
                        "run": late_run,
                    },
                )
    finally:
        await cleanup(factory, ids)
        await engine.dispose()


def test_collection_run_metadata_contains_window_config_pair_constraint():
    constraints = {
        constraint.name: str(constraint.sqltext)
        for constraint in CollectionRun.__table__.constraints
        if getattr(constraint, "name", None)
    }
    assert constraints["ck_collection_runs_window_config_pair"] == (
        "(resolved_window IS NULL) = (operation_config_hash IS NULL)"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "run_mode,values",
    [
        (CollectionRunMode.NORMAL, {"operation_config": {**rolling_config(), "query": "chips"}}),
        (CollectionRunMode.NORMAL, {"cadence_seconds": 601}),
        (CollectionRunMode.NORMAL, {"cursor_version": 2}),
        (CollectionRunMode.BACKFILL, {"operation_config": {**rolling_config(), "query": "chips"}}),
        (CollectionRunMode.BACKFILL, {"cadence_seconds": 601}),
        (CollectionRunMode.BACKFILL, {"cursor_version": 2}),
    ],
)
async def test_target_revision_rejects_any_pending_continuation(run_mode, values):
    engine = create_async_engine(DB)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ids = await seed(factory, "marketaux", "news_all")
    repository = TargetRepository(factory, build_operation_registry())
    try:
        async with factory.begin() as session:
            target = await session.get(CollectionTarget, ids[2])
            target.operation_config = rolling_config()
            target.config_revision = 2
        loaded = await repository.load_for_execution(ids[2], 2)
        dispatch = TargetDispatch(
            ids[2],
            2,
            1,
            run_mode.value,
            dispatch_identity(ids[2], 2, 1, run_mode.value),
        )
        worker = CollectionControlPlaneWorker(
            factory,
            repository,
            RedisDouble(),
            MockProviderTransport([]),
            environ={"MARKETAUX_API_TOKEN": "synthetic"},
        )
        run_id, cursor = await worker._start_run(loaded, dispatch, run_mode)
        await worker._resolved_run_config(run_id, loaded, cursor, run_mode)
        async with factory.begin() as session:
            run = await session.get(CollectionRun, run_id)
            run.status = "failed"
            run.finished_at = datetime.now(UTC)
        with pytest.raises(TargetRepositoryError, match="target_revision_pending_continuation"):
            await repository.revise(ids[2], 2, values)
    finally:
        await cleanup(factory, ids)
        await engine.dispose()


@pytest.mark.asyncio
async def test_target_revision_succeeds_after_continuation_is_explicitly_cleared():
    engine = create_async_engine(DB)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ids = await seed(factory, "marketaux", "news_all")
    repository = TargetRepository(factory, build_operation_registry())
    try:
        async with factory.begin() as session:
            target = await session.get(CollectionTarget, ids[2])
            target.operation_config = rolling_config()
            target.config_revision = 2
        loaded = await repository.load_for_execution(ids[2], 2)
        dispatch = TargetDispatch(ids[2], 2, 1, "normal", dispatch_identity(ids[2], 2, 1))
        worker = CollectionControlPlaneWorker(
            factory,
            repository,
            RedisDouble(),
            MockProviderTransport([]),
            environ={"MARKETAUX_API_TOKEN": "synthetic"},
        )
        run_id, cursor = await worker._start_run(loaded, dispatch, CollectionRunMode.NORMAL)
        await worker._resolved_run_config(run_id, loaded, cursor, CollectionRunMode.NORMAL)
        async with factory.begin() as session:
            run = await session.get(CollectionRun, run_id)
            run.status = "failed"
            run.finished_at = datetime.now(UTC)
            pending = await session.scalar(
                select(CollectionCursor)
                .where(CollectionCursor.target_id == ids[2])
                .with_for_update()
            )
            pending.continuation = None
            pending.continuation_run_id = None
        assert await repository.revise(ids[2], 2, {"cadence_seconds": 601}) == 3
    finally:
        await cleanup(factory, ids)
        await engine.dispose()


async def create_pending_marketaux_continuation(
    factory, ids, run_mode: CollectionRunMode, slot: int
):
    repository = TargetRepository(factory, build_operation_registry())
    transport = MockProviderTransport(
        [
            response(
                {
                    "data": [news(f"{run_mode.value}-a"), news(f"{run_mode.value}-b")],
                    "meta": {"found": 4},
                }
            ),
            response({}, 500),
        ]
    )
    worker = CollectionControlPlaneWorker(
        factory,
        repository,
        RedisDouble(),
        transport,
        environ={"MARKETAUX_API_TOKEN": "synthetic"},
    )
    dispatch = TargetDispatch(
        ids[2], 1, slot, run_mode.value, dispatch_identity(ids[2], 1, slot, run_mode.value)
    )
    report = await worker.execute(dispatch)
    assert report.status == "retry" and report.run_id is not None
    loaded = await repository.load_for_execution(ids[2], 1)
    await worker._finish_error(
        loaded,
        dispatch,
        report.run_id,
        "operator_review_pending",
        False,
        None,
    )
    return repository, report


@pytest.mark.asyncio
async def test_explicit_continuation_abandon_is_atomic_audited_and_revision_safe():
    engine = create_async_engine(DB)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ids = await seed(factory, "marketaux", "news_all")
    try:
        repository, _ = await create_pending_marketaux_continuation(
            factory, ids, CollectionRunMode.NORMAL, 1
        )
        await create_pending_marketaux_continuation(factory, ids, CollectionRunMode.BACKFILL, 2)
        tokens = await repository.pending_continuations(ids[2], 1)
        assert {token.run_mode for token in tokens} == {"normal", "backfill"}

        with pytest.raises(
            TargetRepositoryError, match="target_continuation_abandon_requires_paused"
        ):
            await repository.abandon_pending_continuations(
                ids[2], 1, tokens, reason_code="operator_reviewed_reset"
            )
        with pytest.raises(TargetRepositoryError, match="target_revision_conflict"):
            await repository.abandon_pending_continuations(
                ids[2], 0, tokens, reason_code="operator_reviewed_reset"
            )

        await repository.pause_for_continuation_abandon(ids[2], 1)
        async with factory() as session:
            before = {
                (row.cursor_version, row.run_mode.value): (row.cursor_value, row.watermark_at)
                for row in await session.scalars(
                    select(CollectionCursor).where(CollectionCursor.target_id == ids[2])
                )
            }
        assert (
            await repository.abandon_pending_continuations(
                ids[2], 1, tokens, reason_code="operator_reviewed_reset"
            )
            == 2
        )
        async with factory() as session:
            cursors = tuple(
                await session.scalars(
                    select(CollectionCursor).where(CollectionCursor.target_id == ids[2])
                )
            )
            assert all(
                row.continuation is None and row.continuation_run_id is None for row in cursors
            )
            assert {
                (row.cursor_version, row.run_mode.value): (row.cursor_value, row.watermark_at)
                for row in cursors
            } == before
            audits = tuple(
                await session.scalars(
                    select(AuditLog).where(
                        AuditLog.target_id == ids[2],
                        AuditLog.action == "collection_continuation_abandoned",
                    )
                )
            )
            assert len(audits) == 2
            for audit in audits:
                assert set(audit.after) == {
                    "config_revision",
                    "cursor_version",
                    "run_mode",
                    "bound_run_id",
                    "continuation_hash",
                    "resolved_window_hash",
                    "operation_config_hash",
                    "reason_code",
                }
                assert len(audit.after["continuation_hash"]) == 64
                assert "query" not in json.dumps(audit.after).lower()
                assert "url" not in json.dumps(audit.after).lower()
        assert await repository.revise(ids[2], 1, {"cadence_seconds": 601}) == 2
        assert (
            await repository.abandon_pending_continuations(
                ids[2], 2, tokens, reason_code="operator_reviewed_reset"
            )
            == 0
        )
    finally:
        await cleanup(factory, ids)
        await engine.dispose()


@pytest.mark.asyncio
async def test_continuation_abandon_rejects_running_and_concurrent_advance():
    engine = create_async_engine(DB)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ids = await seed(factory, "marketaux", "news_all")
    repository = TargetRepository(factory, build_operation_registry())
    try:
        first_worker = CollectionControlPlaneWorker(
            factory,
            repository,
            RedisDouble(),
            MockProviderTransport(
                [
                    response({"data": [news("a"), news("b")], "meta": {"found": 6}}),
                    response({}, 500),
                ]
            ),
            environ={"MARKETAUX_API_TOKEN": "synthetic"},
        )
        dispatch = TargetDispatch(ids[2], 1, 1, "normal", dispatch_identity(ids[2], 1, 1))
        first = await first_worker.execute(dispatch)
        assert first.status == "retry" and first.run_id is not None
        stale_tokens = await repository.pending_continuations(ids[2], 1)
        with pytest.raises(TargetRepositoryError, match="target_revision_in_flight"):
            await repository.abandon_pending_continuations(
                ids[2], 1, stale_tokens, reason_code="continuation_unrecoverable"
            )

        second_worker = CollectionControlPlaneWorker(
            factory,
            repository,
            RedisDouble(),
            MockProviderTransport(
                [
                    response({"data": [news("c"), news("d")], "meta": {"found": 6}}),
                    response({}, 500),
                ]
            ),
            environ={"MARKETAUX_API_TOKEN": "synthetic"},
        )
        second = await second_worker.execute(dispatch)
        assert second.status == "partial" and second.run_id == first.run_id
        await repository.pause_for_continuation_abandon(ids[2], 1)
        with pytest.raises(TargetRepositoryError, match="target_continuation_changed"):
            await repository.abandon_pending_continuations(
                ids[2], 1, stale_tokens, reason_code="continuation_unrecoverable"
            )
    finally:
        await cleanup(factory, ids)
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("stale", [False, True])
async def test_rolling_retry_restart_reuses_frozen_window(monkeypatch, stale):
    import market_intelligence.collection.control_plane as cp

    class Clock(datetime):
        current = NOW.replace(hour=23, minute=59, second=30)

        @classmethod
        def now(cls, tz=None):
            return cls.current

    monkeypatch.setattr(cp, "datetime", Clock)
    engine = create_async_engine(DB)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ids = await seed(factory, "marketaux", "news_all")
    try:
        async with factory.begin() as session:
            target = await session.get(CollectionTarget, ids[2])
            target.operation_config = rolling_config()
            target.config_revision += 1
        transport = MockProviderTransport(
            [
                response({"data": [news("a"), news("b")], "meta": {"found": 3}}),
                response({}, 500),
                response({"data": [news("c")], "meta": {"found": 3}}),
            ]
        )
        worker = CollectionControlPlaneWorker(
            factory,
            TargetRepository(factory, build_operation_registry()),
            RedisDouble(),
            transport,
            environ={"MARKETAUX_API_TOKEN": "synthetic"},
        )
        dispatch = TargetDispatch(ids[2], 2, 1, "normal", dispatch_identity(ids[2], 2, 1, "normal"))
        result = await worker.execute(dispatch)
        assert result.status == "retry"
        Clock.current += timedelta(seconds=31)
        if stale:
            async with factory.begin() as session:
                run = await session.get(CollectionRun, result.run_id)
                run.status = cp.CollectionRunStatus.FAILED
                run.finished_at = Clock.current
            dispatch = TargetDispatch(
                ids[2], 2, 2, "normal", dispatch_identity(ids[2], 2, 2, "normal")
            )
        final = await worker.execute(dispatch)
        assert final.status == "succeeded", final
        assert {call.params["published_before"] for call in transport.calls} == {"2026-01-03"}
        assert [call.params["page"] for call in transport.calls] == [1, 2, 2]
    finally:
        await cleanup(factory, ids)
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["append", "earlier", "revision", "reorder"])
async def test_sec_file_keyset_mutations_and_completion(change):
    adapter = BreadthAdapter(
        "sec_edgar", "submissions_recent", RuntimeCredential("SEC_USER_AGENT", "synthetic")
    )
    base = {
        "accessionNumber": "0001045810-26-000001",
        "filingDate": "2026-01-02",
        "form": "8-K",
        "primaryDocument": "a.htm",
    }
    original = [base, {**base, "accessionNumber": "0001045810-26-000002"}]

    def body(items):
        return {
            "filings": {
                "recent": {k: [r[k] for r in items] for k in base},
                "files": [
                    {
                        "name": "CIK0001045810-submissions-001.json",
                        "filingFrom": "2026-01-01",
                        "filingTo": "2026-01-03",
                    }
                ],
            }
        }

    first = await adapter.fetch(
        request("sec_edgar", "submissions_recent", 1),
        MockProviderTransport([response(body(original))]),
    )
    changed = [dict(r) for r in original]
    if change == "append":
        changed.append({**base, "accessionNumber": "0001045810-26-000003"})
    elif change == "earlier":
        changed.insert(
            0, {**base, "accessionNumber": "0001045810-26-000000", "filingDate": "2026-01-01"}
        )
    elif change == "revision":
        changed[0]["primaryDocument"] = "revision.htm"
    else:
        changed.reverse()
    continuation = first.continuation
    for _ in range(6):
        history = bool(continuation["state"].get("file"))
        payload = body(original)["filings"]["recent"] if history else body(changed)
        result = await adapter.fetch(
            replace(request("sec_edgar", "submissions_recent", 1), continuation=continuation),
            MockProviderTransport([response(payload)]),
        )
        assert not result.safe_errors
        if not result.has_more:
            assert history
            break
        continuation = result.continuation
    else:
        pytest.fail("SEC keyset failed to complete")
    overlap = await adapter.fetch(
        request("sec_edgar", "submissions_recent", 10),
        MockProviderTransport([response(body(changed))]),
    )
    assert len(overlap.raw_items) == len(changed)


@pytest.mark.parametrize(
    "provider,operation,foreign_state",
    [
        ("marketaux", "news_all", {"offset": 1}),
        ("finnhub", "company_news", {"page": 2}),
        ("eia", "electricity_retail_sales", {"last_key": ["a", "b"]}),
        ("eia", "electricity_rto_region_data", {"file": "x"}),
        ("sec_edgar", "submissions_recent", {"offset": 1}),
    ],
)
def test_exact_continuation_codec_rejects_cross_operation_state(provider, operation, foreign_state):
    req = request(provider, operation)
    lineage = request_lineage(req, operation)
    valid = encode_continuation(provider, operation, req.config, lineage, {})
    valid["state"] = foreign_state
    with pytest.raises(ContinuationContractError):
        decode_continuation(valid, provider, operation, req.config, lineage)


def test_continuation_rejects_unknown_lineage_and_cross_cik_file():
    req = request("sec_edgar", "submissions_recent")
    lineage = request_lineage(req, "submissions_recent")
    value = encode_continuation(
        "sec_edgar",
        "submissions_recent",
        req.config,
        lineage,
        {
            "file": "CIK0001045810-submissions-001.json",
            "files": [],
        },
    )
    value["lineage"]["unknown"] = 1
    with pytest.raises(ContinuationContractError):
        decode_continuation(value, "sec_edgar", "submissions_recent", req.config, lineage)
    value = encode_continuation(
        "sec_edgar",
        "submissions_recent",
        req.config,
        lineage,
        {
            "file": "CIK0001045810-submissions-001.json",
            "files": [],
        },
    )
    value["state"]["file"] = "CIK9999999999-submissions-001.json"
    with pytest.raises(ContinuationContractError):
        decode_continuation(value, "sec_edgar", "submissions_recent", req.config, lineage)


@pytest.mark.asyncio
@pytest.mark.parametrize("history", [False, True])
async def test_sec_required_column_lengths_fail_closed_and_empty_is_valid(history):
    adapter = BreadthAdapter(
        "sec_edgar", "submissions_recent", RuntimeCredential("SEC_USER_AGENT", "synthetic")
    )
    required = {
        "accessionNumber": [],
        "filingDate": [],
        "form": [],
        "primaryDocument": [],
    }
    body = required if history else {"filings": {"recent": required, "files": []}}
    req = request("sec_edgar", "submissions_recent")
    if history:
        lineage = request_lineage(req, "submissions_recent")
        req = replace(
            req,
            continuation=encode_continuation(
                "sec_edgar",
                "submissions_recent",
                req.config,
                lineage,
                {"file": "CIK0001045810-submissions-001.json", "files": []},
            ),
        )
    valid = await adapter.fetch(req, MockProviderTransport([response(body)]))
    assert not valid.safe_errors and not valid.raw_items
    broken = json.loads(json.dumps(body))
    columns = broken if history else broken["filings"]["recent"]
    columns["form"] = ["8-K"]
    invalid = await adapter.fetch(req, MockProviderTransport([response(broken)]))
    assert invalid.safe_errors and not invalid.raw_items


@pytest.mark.asyncio
async def test_pagination_completion_inconsistency_fails_closed():
    marketaux = BreadthAdapter(
        "marketaux", "news_all", RuntimeCredential("MARKETAUX_API_TOKEN", "synthetic")
    )
    bad_news = await marketaux.fetch(
        request("marketaux", "news_all"),
        MockProviderTransport([response({"data": [], "meta": {"found": 1}})]),
    )
    eia = BreadthAdapter(
        "eia", "electricity_retail_sales", RuntimeCredential("EIA_API_KEY", "synthetic")
    )
    bad_eia = await eia.fetch(
        request("eia", "electricity_retail_sales"),
        MockProviderTransport([response({"response": {"data": [], "total": 1}})]),
    )
    assert bad_news.safe_errors and bad_eia.safe_errors
    assert bad_news.continuation is None and bad_eia.continuation is None


def test_monthly_period_count_lag_overlap_and_future_watermark():
    base = {
        k: v
        for k, v in CONFIGS["eia", "electricity_retail_sales"].items()
        if k not in {"start", "end"}
    }
    for lookback, expected_start in ((1, "2025-12-01"), (12, "2025-01-01")):
        config = {
            **base,
            "window_mode": "rolling_window",
            "lookback_months": lookback,
            "overlap_months": 0,
            "ingestion_lag_months": 0,
            "granularity": "month",
        }
        assert resolve_window("electricity_retail_sales", config, NOW)["start"] == expected_start
    overlap = {
        **base,
        "window_mode": "rolling_window",
        "lookback_months": 3,
        "overlap_months": 1,
        "ingestion_lag_months": 1,
        "granularity": "month",
    }
    resolved = resolve_window(
        "electricity_retail_sales",
        overlap,
        NOW,
        datetime(2030, 1, 1, tzinfo=UTC),
    )
    assert resolved == {"start": "2025-11-01", "end": "2025-11-01"}


@pytest.mark.parametrize(
    "start,end,valid",
    [
        ("2026-01-01", "2026-01-01", True),
        ("2026-01-01", "2026-12-01", True),
        ("2026-01-01", "2027-01-01", False),
        ("2026-01-15", "2026-02-01", False),
        ("2026-01-01", "2026-01-31", False),
        ("2026-01-01T00", "2026-02-01T00", False),
    ],
)
def test_monthly_fixed_window_requires_inclusive_month_starts(start, end, valid):
    config = {
        **CONFIGS["eia", "electricity_retail_sales"],
        "start": start,
        "end": end,
    }
    if valid:
        assert breadth_config("eia", "electricity_retail_sales", config)["end"] == end
    else:
        with pytest.raises(ValueError, match="breadth_window_invalid"):
            breadth_config("eia", "electricity_retail_sales", config)
