# ruff: noqa: E501
from __future__ import annotations

import json
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import select, text
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
from market_intelligence.db.models import (
    AuditLog,
    CollectionCursor,
    CollectionRun,
    CollectionTarget,
    ContentItem,
    EvidenceItem,
    RawItem,
    RawItemObservation,
    SafeFactProjection,
)
from market_intelligence.evidence.handoff import EvidenceProjectionHandoffWorker
from market_intelligence.providers.breadth import BreadthAdapter
from market_intelligence.providers.breadth_config import breadth_config
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
        "end": "2026-01-31",
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


@pytest.mark.asyncio
async def test_marketaux_page_and_failure_preserve_continuation():
    transport = MockProviderTransport(
        [response({"data": [news("a"), news("b")], "meta": {"found": 4}}), response({}, 500)]
    )
    adapter = BreadthAdapter(
        "marketaux", "news_all", RuntimeCredential("MARKETAUX_API_TOKEN", "synthetic")
    )
    first = await adapter.fetch(request("marketaux", "news_all"), transport)
    assert first.has_more and first.continuation["page"] == 2
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
    source, account, target = ids
    async with factory.begin() as session:
        await session.execute(
            text("DELETE FROM audit_logs WHERE target_id=:target"), {"target": target}
        )
        await session.execute(
            text(
                "DELETE FROM evidence_projection_links WHERE safe_fact_projection_id IN (SELECT p.id FROM safe_fact_projections p JOIN raw_items r ON r.id=p.raw_item_id WHERE r.source_id=:source)"
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
        for table in ("raw_item_observations", "raw_items", "collection_runs"):
            await session.execute(
                text(f"DELETE FROM {table} WHERE source_id=:source"), {"source": source}
            )
        await session.execute(
            text("DELETE FROM collection_cursors WHERE source_account_id=:account"),
            {"account": account},
        )
        await session.execute(
            text("DELETE FROM collection_targets WHERE id=:target"), {"target": target}
        )
        await session.execute(
            text("DELETE FROM source_accounts WHERE id=:account"), {"account": account}
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
                assert cursor.continuation["page"] == 2
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
            assert cursor.continuation["page"] == 2
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
        "end": "2026-01-01",
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
        loaded = await TargetRepository(factory, build_operation_registry()).load_for_execution(
            ids[2], 2
        )
        frozen = await worker._resolved_run_config(runs[0].id, loaded, None)
        assert frozen["end"] == "2026-01-03"
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
        history = bool(continuation.get("file"))
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
