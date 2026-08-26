# ruff: noqa: E501
from __future__ import annotations

import json
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from market_intelligence.collection.adapter_factory import UnifiedAdapterFactory
from market_intelligence.collection.control_plane import (
    CollectionControlPlaneWorker,
    TargetDispatch,
    dispatch_identity,
)
from market_intelligence.collection.target_configs import build_operation_registry
from market_intelligence.collection.target_repository import TargetRepository
from market_intelligence.db.models import (
    CollectionCursor,
    CollectionRun,
    ContentItem,
    EvidenceItem,
    RawItem,
    RawItemObservation,
)
from market_intelligence.evidence.handoff import EvidenceProjectionHandoffWorker
from market_intelligence.providers.breadth import BreadthAdapter
from market_intelligence.providers.breadth_config import breadth_config
from market_intelligence.providers.contracts import ProviderFetchRequest, ProviderTransportResponse
from market_intelligence.providers.credentials import RuntimeCredential
from market_intelligence.providers.transport import MockProviderTransport
from market_intelligence.safe_projection.worker import SafeFactProjectionWorker

DB = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://market_intelligence:local_dev_only@localhost:5432/m2a_test_20260826",
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
CREDENTIALS = {
    "marketaux": "MARKETAUX_API_TOKEN",
    "finnhub": "FINNHUB_API_KEY",
    "eia": "EIA_API_KEY",
    "sec_edgar": "SEC_USER_AGENT",
}


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
          VALUES (:key,:source,:account,:operation,'provider_cursor_v1',2,2,CAST(:config AS jsonb),'active',300,2,:pages,:pages,1000000,10,60,'compound','incremental','disabled','ignore',:key,:now,'unknown') RETURNING id"""),
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
    assert result.safe_errors and not result.raw_items


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
async def test_company_news_array_continuation_refuses_changed_snapshot():
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
    assert second.safe_errors and not second.raw_items


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
    assert result.safe_errors and not result.raw_items


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
