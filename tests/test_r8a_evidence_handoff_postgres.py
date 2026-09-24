# ruff: noqa: E501

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from alembic.config import Config
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import market_intelligence.evidence.handoff as handoff_module
from market_intelligence.db.models import (
    BodyAvailability,
    ContentItem,
    ContentKind,
    DeletedStatus,
    EvidenceItem,
    EvidenceProjectionLink,
    EvidenceProjectionLinkStatus,
    RawItem,
    RawItemObservation,
    SafeFactProjection,
)
from market_intelligence.evidence.handoff import EvidenceProjectionHandoffWorker
from market_intelligence.evidence.provider_mappings import (
    map_eia_energy_row_to_evidence,
    map_finnhub_quote_to_evidence,
    map_marketaux_news_to_evidence,
    map_sec_filing_to_evidence,
)
from market_intelligence.evidence.write_path import (
    EvidenceWriteRequest,
    EvidenceWriteService,
    EvidenceWriteStatus,
)
from market_intelligence.rich_evidence import (
    RichEvidenceError,
    RichEvidencePacketBuilder,
    canonical_packet_bytes,
)
from market_intelligence.rich_evidence.builder import packet_query_budget
from market_intelligence.rich_evidence.contracts import (
    EiaRetailFacts,
    EiaRtoFacts,
    FinnhubCompanyNewsFacts,
    FinnhubQuoteFacts,
    MarketauxNewsFacts,
    SecFilingFacts,
)
from market_intelligence.rich_evidence.migration_gate import controlled_upgrade_0010
from market_intelligence.rich_evidence.migration_preflight import validate_0010_pre_migration
from market_intelligence.safe_projection.contracts import (
    canonical_projection_hash,
    normalize_and_classify_factual_payload,
)
from market_intelligence.test_database import isolated_test_database_url

try:
    POSTGRES_TEST_URL = isolated_test_database_url(os.environ.get("TEST_DATABASE_URL"))
except ValueError as exc:
    pytest.skip(str(exc), allow_module_level=True)


def _payload(provider: str, marker: str) -> tuple[str, dict[str, object]]:
    if provider == "marketaux":
        return "news_all", {
            "provider_item_id": marker,
            "published_at": "2026-01-01T00:00:00+00:00",
            "title": "Synthetic factual title",
            "canonical_url": f"https://example.com/{marker}",
            "source_identity": "Synthetic Source",
            "query": "technology",
            "language": "en",
            "symbols": ["NVDA"],
            "description_coverage": "blocked",
            "snippet_coverage": "blocked",
        }
    if provider == "finnhub":
        return "quote", {
            "provider_item_id": "AAPL:1767225600",
            "published_at": "2026-01-01T00:00:00+00:00",
            "symbol": "AAPL",
            "provider_timestamp": 1767225600,
            "c": 101.25,
            "d": -1.0,
            "dp": -0.98,
            "h": 103.0,
            "l": 100.0,
            "o": 102.0,
            "pc": 102.25,
            "currency": "unknown",
            "exchange": "unknown",
        }
    if provider == "eia":
        return "electricity_retail_sales", {
            "provider_item_id": f"2026-01:US:{marker[:4]}",
            "published_at": "2026-01-01T00:00:00+00:00",
            "period": "2026-01",
            "dataset": "electricity",
            "series_identity": f"electricity/retail-sales/us/{marker[:4]}/price",
            "geography": "us",
            "sector": marker[:4],
            "metric": "price",
            "value": 12.345,
            "unit": "unknown",
        }
    return "submissions_recent", {
        "provider_item_id": "0000320193-26-000001",
        "published_at": "2026-01-01T00:00:00+00:00",
        "cik": "0000320193",
        "ticker": "AAPL",
        "accession_number": "0000320193-26-000001",
        "filing_date": "2026-01-01",
        "form": "8-K",
        "primary_document": "a8k.htm",
        "official_url": "https://www.sec.gov/Archives/edgar/data/320193/000032019326000001/a8k.htm",
        "official_source": True,
    }


async def _seed_ready(
    factory: async_sessionmaker[AsyncSession],
    provider: str,
    *,
    raw_id: uuid.UUID | None = None,
    payload_updates: dict[str, object] | None = None,
    operation_payload: tuple[str, dict[str, object]] | None = None,
) -> tuple[uuid.UUID, uuid.UUID, dict[str, object]]:
    marker = uuid.uuid4().hex
    operation, payload = operation_payload or _payload(provider, marker)
    if raw_id is not None and provider == "finnhub" and operation == "quote":
        payload["c"] = 102.5
    if raw_id is not None and provider == "marketaux":
        async with factory() as lookup:
            raw = await lookup.get(RawItem, raw_id)
            assert raw is not None and raw.external_id is not None
            payload["provider_item_id"] = raw.external_id
    if payload_updates:
        payload.update(payload_updates)
    payload, quality = normalize_and_classify_factual_payload(provider, operation, 1, payload)
    projection_hash = canonical_projection_hash(payload)
    async with factory.begin() as session:
        if raw_id is None:
            retention = "link_only" if provider in {"marketaux", "sec_edgar"} else "metadata_only"
            source_id = await session.scalar(
                text("""
                INSERT INTO sources(code,name,source_type,access_method,authorization_status,retention_class,enabled)
                VALUES (:code,'R8A synthetic','api',:provider,'authorized',:retention,true) RETURNING id
            """),
                {"code": f"r8a-{marker}", "provider": provider, "retention": retention},
            )
            account_id = await session.scalar(
                text("""
                INSERT INTO source_accounts(source_id,identity_status,enabled,collection_options)
                VALUES (:source,'verified',true,'{}'::jsonb) RETURNING id
            """),
                {"source": source_id},
            )
            run_id = await session.scalar(
                text("""
                INSERT INTO collection_runs(source_id,source_account_id,started_at,finished_at,status)
                VALUES (:source,:account,:now,:now,'succeeded') RETURNING id
            """),
                {"source": source_id, "account": account_id, "now": datetime.now(UTC)},
            )
            raw_id = await session.scalar(
                text("""
                INSERT INTO raw_items(source_id,source_account_id,collection_run_id,external_id,fetched_at,http_status,content_type,payload_location,payload_hash,retention_class,parse_status)
                VALUES (:source,:account,:run,:external,:now,200,'application/json',:location,:hash,:retention,'pending') RETURNING id
            """),
                {
                    "source": source_id,
                    "account": account_id,
                    "run": run_id,
                    "external": str(payload["provider_item_id"]),
                    "now": datetime.now(UTC),
                    "location": f"internal://r8a/{marker}",
                    "hash": marker.ljust(64, "0")[:64],
                    "retention": retention,
                },
            )
        else:
            raw = await session.get(RawItem, raw_id)
            assert raw is not None
            source_id, account_id = raw.source_id, raw.source_account_id
            run_id = await session.scalar(
                text("""
                INSERT INTO collection_runs(source_id,source_account_id,started_at,finished_at,status)
                VALUES (:source,:account,:now,:now,'succeeded') RETURNING id
            """),
                {"source": source_id, "account": account_id, "now": datetime.now(UTC)},
            )
        contract_version = 2 if operation in {"company_news", "electricity_rto_region_data"} else 1
        observation_id = await session.scalar(
            text("""
            INSERT INTO raw_item_observations(collection_run_id,raw_item_id,source_id,source_account_id,provider,operation_key,provider_contract_version,observed_at,projection_hash,observation_kind)
            VALUES (:run,:raw,:source,:account,:provider,:operation,:contract_version,:now,:hash,'revision_candidate') RETURNING id
        """),
            {
                "run": run_id,
                "raw": raw_id,
                "source": source_id,
                "account": account_id,
                "provider": provider,
                "operation": operation,
                "contract_version": contract_version,
                "now": datetime.now(UTC),
                "hash": projection_hash,
            },
        )
        projection_id = await session.scalar(
            text("""
            INSERT INTO safe_fact_projections(observation_id,raw_item_id,provider,operation_key,projection_schema_version,factual_payload,projection_hash,quality_status,processing_status,processed_at)
            VALUES (:observation,:raw,:provider,:operation,1,CAST(:payload AS jsonb),:hash,:quality,'ready',:now) RETURNING id
        """),
            {
                "observation": observation_id,
                "raw": raw_id,
                "provider": provider,
                "operation": operation,
                "payload": __import__("json").dumps(payload),
                "hash": projection_hash,
                "quality": quality,
                "now": datetime.now(UTC),
            },
        )
    assert raw_id and projection_id
    return raw_id, projection_id, payload


async def _cleanup(factory: async_sessionmaker[AsyncSession]) -> None:
    # Row-level deletion is deliberately forbidden for LINKED lineage. The test suite
    # owns an isolated disposable database, so reset fixtures with PostgreSQL TRUNCATE
    # rather than adding an application-visible bypass to the production guard.
    async with factory.begin() as session:
        await session.execute(text("TRUNCATE TABLE sources CASCADE"))


async def _link_with_explicit_evidence_id(
    factory: async_sessionmaker[AsyncSession],
    projection_id: uuid.UUID,
    evidence_id: uuid.UUID,
) -> None:
    """Create deterministic UUID ordering for packet keyset behavior tests."""
    async with factory.begin() as session:
        projection = await session.get(SafeFactProjection, projection_id)
        assert projection is not None
        observation = await session.get(RawItemObservation, projection.observation_id)
        raw = await session.get(RawItem, projection.raw_item_id)
        assert observation is not None and raw is not None
        content = await handoff_module._content(session, projection, raw)
        evidence = await handoff_module._evidence(session, projection, observation, raw, content)
        original_id = evidence.id
        await session.execute(
            text("UPDATE evidence_items SET id=:new_id WHERE id=:old_id"),
            {"new_id": evidence_id, "old_id": original_id},
        )
        session.expunge(evidence)
        session.add(
            EvidenceProjectionLink(
                safe_fact_projection_id=projection.id,
                evidence_item_id=evidence_id,
                content_item_id=None if content is None else content.id,
                status=EvidenceProjectionLinkStatus.LINKED,
                attempt_count=1,
                next_retry_at=None,
                safe_error_code=None,
                linked_at=datetime.now(UTC),
                canonical_evidence=True,
                canonical_content=content is not None,
            )
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider,content_count,access_level",
    [
        ("marketaux", 1, "link_only"),
        ("finnhub", 0, "licensed"),
        ("eia", 0, "public_summary"),
        ("sec_edgar", 1, "link_only"),
    ],
)
async def test_ready_projection_links_canonical_evidence(
    provider: str, content_count: int, access_level: str
) -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        raw_id, projection_id, payload = await _seed_ready(factory, provider)
        report = await EvidenceProjectionHandoffWorker(factory).process_batch(limit=10)
        assert report.linked == 1
        async with factory() as session:
            link = await session.scalar(
                select(EvidenceProjectionLink).where(
                    EvidenceProjectionLink.safe_fact_projection_id == projection_id
                )
            )
            assert link is not None and link.status is EvidenceProjectionLinkStatus.LINKED
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(EvidenceItem)
                    .where(EvidenceItem.raw_item_id == raw_id)
                )
                == 1
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(ContentItem)
                    .where(ContentItem.raw_item_id == raw_id)
                )
                == content_count
            )
            evidence = await session.scalar(
                select(EvidenceItem).where(EvidenceItem.raw_item_id == raw_id)
            )
            assert evidence is not None and evidence.access_level == access_level
            stored = await session.get(SafeFactProjection, projection_id)
            assert stored is not None and stored.factual_payload == payload
            if provider in {"finnhub", "eia"}:
                assert "value" in payload or payload["c"] == 101.25
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(ContentItem)
                        .where(ContentItem.raw_item_id == raw_id)
                    )
                    == 0
                )
    finally:
        await _cleanup(factory)
        await engine.dispose()


@pytest.mark.asyncio
async def test_revision_and_concurrent_reconciliation_are_idempotent() -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        raw_id, first, _ = await _seed_ready(factory, "finnhub")
        _, second, _ = await _seed_ready(factory, "finnhub", raw_id=raw_id)
        reports = await asyncio.gather(
            *[EvidenceProjectionHandoffWorker(factory).process_batch(limit=1) for _ in range(3)]
        )
        assert sum(item.linked for item in reports) == 2
        async with factory() as session:
            links = tuple(
                await session.scalars(
                    select(EvidenceProjectionLink).where(
                        EvidenceProjectionLink.safe_fact_projection_id.in_((first, second))
                    )
                )
            )
            assert len(links) == 2
            assert len({link.evidence_item_id for link in links}) == 1
            projections = tuple(
                await session.scalars(
                    select(SafeFactProjection).where(SafeFactProjection.id.in_((first, second)))
                )
            )
            assert len({item.projection_hash for item in projections}) == 2
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(EvidenceItem)
                    .where(EvidenceItem.raw_item_id == raw_id)
                )
                == 1
            )
    finally:
        await _cleanup(factory)
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "table,assignment",
    [
        ("safe_fact_projections", "quality_status='partial'"),
        ("raw_item_observations", "projection_hash=repeat('0',64)"),
        ("raw_items", "retention_class='metadata_only'"),
        ("sources", "retention_class='metadata_only'"),
    ],
)
async def test_handoff_revalidates_mutation_committed_while_waiting_for_locks(
    table: str, assignment: str
) -> None:
    """A second transaction may change pre-lock state, but it cannot be consumed."""
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    mutator = factory()
    transaction = await mutator.begin()
    try:
        raw_id, projection_id, _ = await _seed_ready(factory, "marketaux")
        # Materialize the durable pending handoff before the mutator wins a
        # source-row lock. This tests post-claim revalidation rather than
        # allowing discovery to skip an in-flight projection altogether.
        async with factory.begin() as session:
            session.add(
                EvidenceProjectionLink(
                    safe_fact_projection_id=projection_id,
                    status=EvidenceProjectionLinkStatus.PENDING,
                )
            )
        projection = await mutator.get(SafeFactProjection, projection_id)
        raw = await mutator.get(RawItem, raw_id)
        assert projection is not None and raw is not None
        identities = {
            "safe_fact_projections": projection_id,
            "raw_item_observations": projection.observation_id,
            "raw_items": raw_id,
            "sources": raw.source_id,
        }
        # The mutation obtains the target row lock first through ordinary SQL.
        # No test-only advisory ordering is allowed to hide row/advisory inversion.
        await mutator.execute(
            text(f"UPDATE {table} SET {assignment} WHERE id=:id"),
            {"id": identities[table]},
        )
        handoff = asyncio.create_task(
            EvidenceProjectionHandoffWorker(factory).process_batch(limit=1)
        )
        await asyncio.sleep(0.05)
        await transaction.commit()
        report = await asyncio.wait_for(handoff, timeout=5)
        assert report.blocked == 1
        async with factory() as session:
            link = await session.scalar(
                select(EvidenceProjectionLink).where(
                    EvidenceProjectionLink.safe_fact_projection_id == projection_id
                )
            )
            assert link is not None
            assert link.status is EvidenceProjectionLinkStatus.BLOCKED
            assert link.evidence_item_id is None
            assert link.content_item_id is None
    finally:
        if transaction.is_active:
            await transaction.rollback()
        await mutator.close()
        await _cleanup(factory)
        await engine.dispose()


@pytest.mark.asyncio
async def test_non_ready_is_not_discovered_and_stale_is_recovered() -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        _, projection_id, _ = await _seed_ready(factory, "marketaux")
        async with factory.begin() as session:
            projection = await session.get(SafeFactProjection, projection_id)
            assert projection is not None
            projection.processing_status = "blocked"
        ready_raw, ready_projection, _ = await _seed_ready(factory, "finnhub")
        report = await EvidenceProjectionHandoffWorker(factory).process_batch(limit=1)
        assert report.linked == 1
        async with factory() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(EvidenceProjectionLink)
                    .where(EvidenceProjectionLink.safe_fact_projection_id == projection_id)
                )
                == 0
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(EvidenceItem)
                    .where(EvidenceItem.raw_item_id == ready_raw)
                )
                == 1
            )
            assert await session.scalar(
                select(EvidenceProjectionLink.id).where(
                    EvidenceProjectionLink.safe_fact_projection_id == ready_projection
                )
            )
    finally:
        await _cleanup(factory)
        await engine.dispose()


@pytest.mark.asyncio
async def test_marketaux_partial_does_not_invent_content() -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        raw_id, _, _ = await _seed_ready(factory, "marketaux", payload_updates={"title": None})
        report = await EvidenceProjectionHandoffWorker(factory).process_batch(limit=1)
        assert report.linked == 1
        async with factory() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(EvidenceItem)
                    .where(EvidenceItem.raw_item_id == raw_id)
                )
                == 1
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(ContentItem)
                    .where(ContentItem.raw_item_id == raw_id)
                )
                == 0
            )
    finally:
        await _cleanup(factory)
        await engine.dispose()


@pytest.mark.asyncio
async def test_stale_processing_recovers_and_links() -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        _, projection_id, _ = await _seed_ready(factory, "eia")
        async with factory.begin() as session:
            session.add(
                EvidenceProjectionLink(
                    safe_fact_projection_id=projection_id,
                    status=EvidenceProjectionLinkStatus.PROCESSING,
                    attempt_count=1,
                    updated_at=datetime.now(UTC) - timedelta(hours=1),
                )
            )
        report = await EvidenceProjectionHandoffWorker(
            factory, stale_after=timedelta(minutes=1)
        ).process_batch(limit=1)
        assert report.recovered == 1
        assert report.linked == 1
    finally:
        await _cleanup(factory)
        await engine.dispose()


@pytest.mark.asyncio
async def test_identity_conflict_blocks_and_rolls_back_new_content() -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        raw_id, projection_id, _ = await _seed_ready(factory, "marketaux")
        async with factory.begin() as session:
            raw = await session.get(RawItem, raw_id)
            assert raw is not None
            session.add(
                EvidenceItem(
                    evidence_version=1,
                    provider="marketaux",
                    provider_item_type="marketaux_news",
                    evidence_kind="news",
                    source_type="news",
                    source_id=raw.source_id,
                    source_account_id=raw.source_account_id,
                    raw_item_id=raw.id,
                    content_item_id=None,
                    provider_item_id="conflicting-identity",
                    provider_item_hash="f" * 64,
                    event_time=None,
                    observed_at=datetime.now(UTC),
                    access_level="public_summary",
                    processing_status="validated",
                    official_source_flag=False,
                    market_data_flag=False,
                    disclosure_flag=False,
                    news_signal_flag=True,
                    content_presence={},
                    numeric_presence={},
                    entity_refs=[],
                    asset_refs=[],
                    topic_refs=[],
                    raw_payload_reference=None,
                    errors=[],
                )
            )
        report = await EvidenceProjectionHandoffWorker(factory).process_batch(limit=1)
        assert report.blocked == 1
        async with factory() as session:
            link = await session.scalar(
                select(EvidenceProjectionLink).where(
                    EvidenceProjectionLink.safe_fact_projection_id == projection_id
                )
            )
            assert link is not None
            assert link.status is EvidenceProjectionLinkStatus.BLOCKED
            assert link.safe_error_code == "evidence_canonical_identity_conflict"
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(ContentItem)
                    .where(ContentItem.raw_item_id == raw_id)
                )
                == 0
            )
    finally:
        await _cleanup(factory)
        await engine.dispose()


def _legacy_envelope(
    provider: str, payload: dict[str, object], *, observed_at: datetime | None = None
) -> object:
    context: dict[str, object] = {"observed_at": observed_at or datetime.now(UTC)}
    if provider == "marketaux":
        return map_marketaux_news_to_evidence(
            {
                "uuid": payload["provider_item_id"],
                "title": payload.get("title"),
                "url": payload.get("canonical_url"),
                "published_at": payload["published_at"],
            },
            context,
        )
    if provider == "finnhub":
        context["symbol"] = payload["symbol"]
        return map_finnhub_quote_to_evidence(
            {key: payload[key] for key in ("c", "d", "dp", "h", "l", "o", "pc")}
            | {"t": payload["provider_timestamp"]},
            context,
        )
    if provider == "eia":
        return map_eia_energy_row_to_evidence(
            {
                "period": payload["period"],
                "stateid": payload["geography"],
                "sectorid": payload["sector"],
                "price": payload["value"],
            },
            context,
        )
    context["ticker"] = payload["ticker"]
    return map_sec_filing_to_evidence(
        {
            "accessionNumber": payload["accession_number"],
            "filingDate": payload["filing_date"],
            "form": payload["form"],
            "primaryDocument": payload["primary_document"],
        },
        context,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["marketaux", "finnhub", "eia", "sec_edgar"])
async def test_real_legacy_mapper_evidence_is_adopted(provider: str) -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        raw_id, projection_id, payload = await _seed_ready(factory, provider)
        async with factory.begin() as session:
            raw = await session.get(RawItem, raw_id)
            projection = await session.get(SafeFactProjection, projection_id)
            assert raw is not None and projection is not None
            observation = await session.get(RawItemObservation, projection.observation_id)
            assert observation is not None
            outcome = await EvidenceWriteService(session).write_one(
                EvidenceWriteRequest(
                    envelope=_legacy_envelope(  # type: ignore[arg-type]
                        provider, payload, observed_at=observation.observed_at
                    ),
                    source_id=raw.source_id,
                    source_account_id=raw.source_account_id,
                    raw_item_id=raw.id,
                )
            )
            assert outcome.status is EvidenceWriteStatus.INSERTED
            legacy_id = outcome.evidence_item_id
        report = await EvidenceProjectionHandoffWorker(factory).process_batch(limit=1)
        assert report.linked == 1
        async with factory() as session:
            link = await session.scalar(
                select(EvidenceProjectionLink).where(
                    EvidenceProjectionLink.safe_fact_projection_id == projection_id
                )
            )
            assert link is not None and link.evidence_item_id == legacy_id
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(EvidenceItem)
                    .where(EvidenceItem.raw_item_id == raw_id)
                )
                == 1
            )
    finally:
        await _cleanup(factory)
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("factual_payload", {"provider_item_id": "corrupt"}),
        ("projection_hash", "0" * 64),
        ("projection_schema_version", 99),
        ("provider", "finnhub"),
        ("operation_key", "quote"),
    ],
)
async def test_ready_projection_is_revalidated_before_handoff(field: str, value: object) -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        raw_id, projection_id, _ = await _seed_ready(factory, "marketaux")
        async with factory.begin() as session:
            await session.execute(
                text(
                    "ALTER TABLE safe_fact_projections DISABLE TRIGGER trg_r2_projection_provenance_guard"
                )
            )
            if field == "factual_payload":
                await session.execute(
                    text(
                        "UPDATE safe_fact_projections "
                        "SET factual_payload=CAST(:value AS jsonb) WHERE id=:id"
                    ),
                    {"value": json.dumps(value), "id": projection_id},
                )
            else:
                await session.execute(
                    text(f"UPDATE safe_fact_projections SET {field}=:value WHERE id=:id"),
                    {"value": value, "id": projection_id},
                )
            await session.execute(
                text(
                    "ALTER TABLE safe_fact_projections ENABLE TRIGGER trg_r2_projection_provenance_guard"
                )
            )
        report = await EvidenceProjectionHandoffWorker(factory).process_batch(limit=1)
        assert report.blocked == 1
        async with factory() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(EvidenceItem)
                    .where(EvidenceItem.raw_item_id == raw_id)
                )
                == 0
            )
            link = await session.scalar(
                select(EvidenceProjectionLink).where(
                    EvidenceProjectionLink.safe_fact_projection_id == projection_id
                )
            )
            assert link is not None
            assert link.safe_error_code == "evidence_projection_contract_invalid"
    finally:
        await _cleanup(factory)
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["marketaux", "sec_edgar"])
async def test_content_revision_keeps_first_canonical_content(provider: str) -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        raw_id, first_projection, _ = await _seed_ready(factory, provider)
        assert (await EvidenceProjectionHandoffWorker(factory).process_batch(limit=1)).linked == 1
        async with factory() as session:
            first_content = await session.scalar(
                select(ContentItem).where(ContentItem.raw_item_id == raw_id)
            )
            first_evidence = await session.scalar(
                select(EvidenceItem).where(EvidenceItem.raw_item_id == raw_id)
            )
            assert first_content is not None and first_evidence is not None
            original_title, original_url = first_content.title, first_content.canonical_url
        updates = (
            {"title": "Revised synthetic title", "canonical_url": "https://example.com/revised"}
            if provider == "marketaux"
            else {
                "primary_document": "revised8k.htm",
                "official_url": "https://www.sec.gov/Archives/edgar/data/320193/000032019326000001/revised8k.htm",
            }
        )
        _, second_projection, _ = await _seed_ready(
            factory, provider, raw_id=raw_id, payload_updates=updates
        )
        assert (await EvidenceProjectionHandoffWorker(factory).process_batch(limit=1)).linked == 1
        async with factory() as session:
            links = tuple(
                await session.scalars(
                    select(EvidenceProjectionLink).where(
                        EvidenceProjectionLink.safe_fact_projection_id.in_(
                            (first_projection, second_projection)
                        )
                    )
                )
            )
            assert len({link.evidence_item_id for link in links}) == 1
            assert len({link.content_item_id for link in links}) == 1
            content = await session.get(ContentItem, first_content.id)
            assert content is not None
            assert (content.title, content.canonical_url) == (original_title, original_url)
            projections = tuple(
                await session.scalars(
                    select(SafeFactProjection).where(
                        SafeFactProjection.id.in_((first_projection, second_projection))
                    )
                )
            )
            assert len({projection.projection_hash for projection in projections}) == 2
    finally:
        await _cleanup(factory)
        await engine.dispose()


async def _unsafe_content(
    session: AsyncSession,
    raw: RawItem,
    payload: dict[str, object],
    *,
    provider: str,
    operation: str,
    kind: ContentKind,
    availability: BodyAvailability = BodyAvailability.UNAVAILABLE,
    url: str | None = None,
) -> ContentItem:
    item = ContentItem(
        raw_item_id=raw.id,
        source_id=raw.source_id,
        source_account_id=raw.source_account_id,
        content_kind=kind,
        external_id=str(payload["provider_item_id"]),
        title=(
            f"SEC {payload['form']} filing"
            if provider == "sec_edgar"
            else str(payload.get("title"))
        ),
        source_summary=None,
        body=None,
        body_availability=availability,
        author=None,
        language=payload.get("language") if provider == "marketaux" else None,
        original_url=url,
        canonical_url=url,
        source_published_at=datetime.fromisoformat(str(payload["published_at"])),
        source_updated_at=None,
        first_seen_at=raw.fetched_at,
        content_hash=None,
        reply_to_external_id=None,
        quote_external_id=None,
        repost_external_id=None,
        deleted_status=DeletedStatus.UNKNOWN,
        metadata_={
            "provider": provider,
            "operation_key": operation,
            "retention": raw.retention_class,
        },
    )
    session.add(item)
    await session.flush()
    return item


async def _prepare_direct_marketaux_link(
    factory: async_sessionmaker[AsyncSession],
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    """Build an otherwise-valid direct LINKED transition fixture."""
    raw_id, projection_id, payload = await _seed_ready(factory, "marketaux")
    async with factory.begin() as session:
        raw = await session.get(RawItem, raw_id)
        projection = await session.get(SafeFactProjection, projection_id)
        assert raw is not None and projection is not None
        observation = await session.get(RawItemObservation, projection.observation_id)
        assert observation is not None
        content = await _unsafe_content(
            session,
            raw,
            payload,
            provider="marketaux",
            operation="news_all",
            kind=ContentKind.ARTICLE,
            availability=BodyAvailability.UNAVAILABLE,
            url=str(payload["canonical_url"]),
        )
        evidence = await handoff_module._evidence(session, projection, observation, raw, content)
        return raw.id, raw.source_id, projection.id, evidence.id, content.id


async def _insert_canonical_link(
    factory: async_sessionmaker[AsyncSession],
    *,
    projection_id: uuid.UUID,
    evidence_id: uuid.UUID,
    content_id: uuid.UUID,
) -> None:
    async with factory.begin() as session:
        await session.execute(
            text("""
            INSERT INTO evidence_projection_links(
              safe_fact_projection_id,evidence_item_id,content_item_id,status,linked_at,
              canonical_evidence,canonical_content
            ) VALUES (:projection,:evidence,:content,'linked',:now,true,true)
            """),
            {
                "projection": projection_id,
                "evidence": evidence_id,
                "content": content_id,
                "now": datetime.now(UTC),
            },
        )


@pytest.mark.asyncio
async def test_database_accepts_complete_marketaux_link_policy_control() -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        _, _, projection_id, evidence_id, content_id = await _prepare_direct_marketaux_link(factory)
        await _insert_canonical_link(
            factory,
            projection_id=projection_id,
            evidence_id=evidence_id,
            content_id=content_id,
        )
    finally:
        await _cleanup(factory)
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("assignment", "parameters"),
    [
        ("content_kind='official_release'", {}),
        ("body_availability='full'", {}),
        ("title='wrong'", {}),
        ("original_url='https://example.com/wrong'", {}),
        ("canonical_url='https://example.com/wrong'", {}),
        ("source_published_at=source_published_at + interval '1 second'", {}),
        ("language='fr'", {}),
        ("metadata=metadata || jsonb_build_object('extra',true)", {}),
        ("metadata=jsonb_set(metadata,'{provider}','\"finnhub\"'::jsonb)", {}),
        ("metadata=jsonb_set(metadata,'{operation_key}','\"quote\"'::jsonb)", {}),
        ("metadata=jsonb_set(metadata,'{retention}','\"metadata_only\"'::jsonb)", {}),
    ],
)
async def test_database_content_policy_guard_rejects_single_field_mutation(
    assignment: str, parameters: dict[str, object]
) -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        _, _, projection_id, evidence_id, content_id = await _prepare_direct_marketaux_link(factory)
        async with factory.begin() as session:
            await session.execute(
                text(f"UPDATE content_items SET {assignment} WHERE id=:id"),
                {"id": content_id, **parameters},
            )
        with pytest.raises(DBAPIError) as failure:
            await _insert_canonical_link(
                factory,
                projection_id=projection_id,
                evidence_id=evidence_id,
                content_id=content_id,
            )
        assert "linked_content_field_policy_invalid" in str(failure.value.orig)
    finally:
        await _cleanup(factory)
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("assignment", "expected"),
    [
        ("access_level='public_summary'", "linked_operation_policy_invalid"),
        ("provider_item_hash=repeat('0',64)", "linked_evidence_identity_policy_invalid"),
        (
            "event_time=event_time + interval '1 second'",
            "linked_evidence_identity_policy_invalid",
        ),
    ],
)
async def test_database_evidence_policy_guard_rejects_single_field_mutation(
    assignment: str, expected: str
) -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        _, _, projection_id, evidence_id, content_id = await _prepare_direct_marketaux_link(factory)
        async with factory.begin() as session:
            await session.execute(
                text(f"UPDATE evidence_items SET {assignment} WHERE id=:id"),
                {"id": evidence_id},
            )
        with pytest.raises(DBAPIError) as failure:
            await _insert_canonical_link(
                factory,
                projection_id=projection_id,
                evidence_id=evidence_id,
                content_id=content_id,
            )
        assert expected in str(failure.value.orig)
    finally:
        await _cleanup(factory)
        await engine.dispose()


@pytest.mark.asyncio
async def test_database_evidence_flag_constraint_is_specific() -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        _, _, _, evidence_id, _ = await _prepare_direct_marketaux_link(factory)
        with pytest.raises(DBAPIError) as failure:
            async with factory.begin() as session:
                await session.execute(
                    text("UPDATE evidence_items SET market_data_flag=true WHERE id=:id"),
                    {"id": evidence_id},
                )
        assert "ck_evidence_items_flags_consistent" in str(failure.value.orig)
    finally:
        await _cleanup(factory)
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("table", ["raw_items", "sources"])
async def test_database_retention_policy_guard_rejects_disallowed_retention(table: str) -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        (
            raw_id,
            source_id,
            projection_id,
            evidence_id,
            content_id,
        ) = await _prepare_direct_marketaux_link(factory)
        async with factory.begin() as session:
            await session.execute(
                text(f"UPDATE {table} SET retention_class='full_content' WHERE id=:id"),
                {"id": raw_id if table == "raw_items" else source_id},
            )
        with pytest.raises(DBAPIError) as failure:
            await _insert_canonical_link(
                factory,
                projection_id=projection_id,
                evidence_id=evidence_id,
                content_id=content_id,
            )
        assert "linked_operation_policy_invalid" in str(failure.value.orig)
    finally:
        await _cleanup(factory)
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "kind", "availability", "url_mode"),
    [
        ("finnhub", ContentKind.ARTICLE, BodyAvailability.UNAVAILABLE, "none"),
        ("eia", ContentKind.OFFICIAL_RELEASE, BodyAvailability.UNAVAILABLE, "none"),
        ("marketaux", ContentKind.OFFICIAL_RELEASE, BodyAvailability.UNAVAILABLE, "payload"),
        ("sec_edgar", ContentKind.ARTICLE, BodyAvailability.UNAVAILABLE, "payload"),
        ("sec_edgar", ContentKind.OFFICIAL_RELEASE, BodyAvailability.FULL, "payload"),
        ("sec_edgar", ContentKind.OFFICIAL_RELEASE, BodyAvailability.UNAVAILABLE, "wrong"),
    ],
)
async def test_database_rejects_provider_content_policy_bypass(
    provider: str,
    kind: ContentKind,
    availability: BodyAvailability,
    url_mode: str,
) -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        raw_id, projection_id, payload = await _seed_ready(factory, provider)
        async with factory.begin() as session:
            raw = await session.get(RawItem, raw_id)
            assert raw is not None
            expected_url = payload.get("official_url") or payload.get("canonical_url")
            url = None if url_mode == "none" else str(expected_url)
            if url_mode == "wrong":
                url = "https://www.sec.gov/Archives/edgar/data/320193/wrong.htm"
            content = await _unsafe_content(
                session,
                raw,
                payload,
                provider=provider,
                operation=(
                    "news_all"
                    if provider == "marketaux"
                    else "submissions_recent"
                    if provider == "sec_edgar"
                    else "quote"
                    if provider == "finnhub"
                    else "electricity_retail_sales"
                ),
                kind=kind,
                availability=availability,
                url=url,
            )
            projection = await session.get(SafeFactProjection, projection_id)
            assert projection is not None
            observation = await session.get(RawItemObservation, projection.observation_id)
            assert observation is not None
            evidence = await handoff_module._evidence(
                session,
                projection,
                observation,
                raw,
                None if provider in {"finnhub", "eia"} else content,
            )
            evidence_id, content_id = evidence.id, content.id
        with pytest.raises(DBAPIError) as failure:
            async with factory.begin() as session:
                await session.execute(
                    text("""
                    INSERT INTO evidence_projection_links(
                      safe_fact_projection_id,evidence_item_id,content_item_id,status,linked_at,
                      canonical_evidence,canonical_content
                    ) VALUES (:projection,:evidence,:content,'linked',:now,true,true)
                    """),
                    {
                        "projection": projection_id,
                        "evidence": evidence_id,
                        "content": content_id,
                        "now": datetime.now(UTC),
                    },
                )
        expected = (
            "linked_operation_policy_invalid"
            if provider in {"finnhub", "eia"}
            else "linked_content_field_policy_invalid"
        )
        assert expected in str(failure.value.orig)
    finally:
        await _cleanup(factory)
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["finnhub", "eia"])
async def test_database_rejects_market_observation_evidence_content(provider: str) -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        raw_id, _, payload = await _seed_ready(factory, provider)
        async with factory.begin() as session:
            raw = await session.get(RawItem, raw_id)
            assert raw is not None
            content = await _unsafe_content(
                session,
                raw,
                payload,
                provider=provider,
                operation="quote" if provider == "finnhub" else "electricity_retail_sales",
                kind=ContentKind.ARTICLE,
                availability=BodyAvailability.UNAVAILABLE,
            )
            outcome = await EvidenceWriteService(session).write_one(
                EvidenceWriteRequest(
                    envelope=_legacy_envelope(provider, payload),  # type: ignore[arg-type]
                    source_id=raw.source_id,
                    source_account_id=raw.source_account_id,
                    raw_item_id=raw.id,
                )
            )
            assert outcome.evidence_item_id is not None
            evidence_id, content_id = outcome.evidence_item_id, content.id
        with pytest.raises(DBAPIError):
            async with factory.begin() as session:
                await session.execute(
                    text("UPDATE evidence_items SET content_item_id=:content WHERE id=:evidence"),
                    {"content": content_id, "evidence": evidence_id},
                )
    finally:
        await _cleanup(factory)
        await engine.dispose()


@pytest.mark.asyncio
async def test_database_rejects_nonlinked_references_and_link_rebinding() -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        raw_id, first_projection, _ = await _seed_ready(factory, "finnhub")
        assert (await EvidenceProjectionHandoffWorker(factory).process_batch(limit=1)).linked == 1
        async with factory() as session:
            link = await session.scalar(
                select(EvidenceProjectionLink).where(
                    EvidenceProjectionLink.safe_fact_projection_id == first_projection
                )
            )
            assert link is not None and link.evidence_item_id is not None
            link_id, evidence_id = link.id, link.evidence_item_id
        with pytest.raises(DBAPIError):
            async with factory.begin() as session:
                await session.execute(
                    text("UPDATE evidence_projection_links SET status='pending' WHERE id=:id"),
                    {"id": link_id},
                )
        with pytest.raises(DBAPIError):
            async with factory.begin() as session:
                await session.execute(
                    text(
                        "UPDATE evidence_projection_links "
                        "SET evidence_item_id=NULL, linked_at=NULL WHERE id=:id"
                    ),
                    {"id": link_id},
                )
        _, second_projection, _ = await _seed_ready(factory, "finnhub", raw_id=raw_id)
        with pytest.raises(DBAPIError):
            async with factory.begin() as session:
                await session.execute(
                    text("""
                    INSERT INTO evidence_projection_links(
                      safe_fact_projection_id,evidence_item_id,status
                    ) VALUES (:projection,:evidence,'pending')
                    """),
                    {"projection": second_projection, "evidence": evidence_id},
                )
        async with factory() as session:
            original = await session.get(EvidenceProjectionLink, link_id)
            assert original is not None and original.status is EvidenceProjectionLinkStatus.LINKED
    finally:
        await _cleanup(factory)
        await engine.dispose()


@pytest.mark.asyncio
async def test_unexpected_item_failure_isolated_and_retry_exhaustion_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    original_content = handoff_module._content

    async def fail_marketaux_only(
        session: AsyncSession, projection: SafeFactProjection, raw: RawItem
    ) -> ContentItem | None:
        if projection.provider == "marketaux":
            raise RuntimeError("sensitive detail must not escape")
        return await original_content(session, projection, raw)

    try:
        _, failed_projection, _ = await _seed_ready(factory, "marketaux")
        _, valid_projection, _ = await _seed_ready(factory, "finnhub")
        monkeypatch.setattr(handoff_module, "_content", fail_marketaux_only)
        worker = EvidenceProjectionHandoffWorker(factory, max_attempts=2, retry_delay=timedelta(0))
        first = await worker.process_batch(limit=2)
        assert (first.linked, first.retried, first.blocked) == (1, 1, 0)
        async with factory() as session:
            failed = await session.scalar(
                select(EvidenceProjectionLink).where(
                    EvidenceProjectionLink.safe_fact_projection_id == failed_projection
                )
            )
            valid = await session.scalar(
                select(EvidenceProjectionLink).where(
                    EvidenceProjectionLink.safe_fact_projection_id == valid_projection
                )
            )
            assert failed is not None and failed.status is EvidenceProjectionLinkStatus.RETRY
            assert failed.safe_error_code == "evidence_handoff_unexpected"
            assert "sensitive" not in str(failed.safe_error_code)
            assert valid is not None and valid.status is EvidenceProjectionLinkStatus.LINKED
        second = await worker.process_batch(limit=2)
        assert second.blocked == 1
        async with factory() as session:
            failed = await session.scalar(
                select(EvidenceProjectionLink).where(
                    EvidenceProjectionLink.safe_fact_projection_id == failed_projection
                )
            )
            assert failed is not None and failed.status is EvidenceProjectionLinkStatus.BLOCKED
            assert failed.safe_error_code == "evidence_handoff_retry_exhausted"
    finally:
        await _cleanup(factory)
        await engine.dispose()


def test_handoff_source_does_not_use_legacy_or_downstream_runtime() -> None:
    source = __import__("pathlib").Path("src/market_intelligence/evidence/handoff.py").read_text()
    for forbidden in (
        "map_marketaux_news_to_evidence",
        "map_finnhub_quote_to_evidence",
        "map_eia_energy_row_to_evidence",
        "map_sec_filing_to_evidence",
        "EvidenceWriteService",
        "EventCandidate",
        "Notification",
        "OpenAI",
        "Telegram",
        "httpx",
        "requests",
    ):
        assert forbidden not in source


def test_rich_evidence_builder_is_read_only_and_legacy_mapper_free() -> None:
    source = (
        __import__("pathlib").Path("src/market_intelligence/rich_evidence/builder.py").read_text()
    )
    for forbidden in (
        "map_marketaux_news_to_evidence",
        "map_finnhub_quote_to_evidence",
        "map_eia_energy_row_to_evidence",
        "map_sec_filing_to_evidence",
        "EvidenceWriteService",
        "EventCandidate",
        "ImpactAnalysis",
        "Notification(",
        "httpx",
        "requests",
        "payload_location",
        "raw_payload_reference",
    ):
        assert forbidden not in source


def _extra_operation_payload(operation: str) -> tuple[str, dict[str, object]]:
    if operation == "company_news":
        return operation, {
            "provider_item_id": "company-news:stable-item",
            "published_at": "2026-01-01T00:00:00+00:00",
            "title": "Synthetic company news",
            "canonical_url": "https://example.com/company-news",
            "source_identity": "Synthetic Source",
            "symbol": "AAPL",
            "category": "company",
            "summary_coverage": "blocked",
        }
    return operation, {
        "provider_item_id": "rto:6575c77c6e751632238ff02e741a338dfe8f930ecec7d31140172bbe3b8e9edc",
        "published_at": "2026-01-01T00:00:00+00:00",
        "period": "2026-01-01T00",
        "dataset": "electricity_rto_region_data",
        "series_identity": "electricity/rto/region-data/CAL/D",
        "region": "CAL",
        "metric": "D",
        "value": 150.25,
        "unit": "megawatthours",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider,operation,expected_type",
    [
        ("marketaux", "news_all", MarketauxNewsFacts),
        ("finnhub", "quote", FinnhubQuoteFacts),
        ("finnhub", "company_news", FinnhubCompanyNewsFacts),
        ("eia", "electricity_retail_sales", EiaRetailFacts),
        ("eia", "electricity_rto_region_data", EiaRtoFacts),
        ("sec_edgar", "submissions_recent", SecFilingFacts),
    ],
)
async def test_rich_evidence_packet_covers_six_typed_operations(
    provider: str, operation: str, expected_type: type[object]
) -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        operation_payload = (
            _extra_operation_payload(operation)
            if operation in {"company_news", "electricity_rto_region_data"}
            else None
        )
        raw_id, _, _ = await _seed_ready(factory, provider, operation_payload=operation_payload)
        assert (await EvidenceProjectionHandoffWorker(factory).process_batch(limit=10)).linked == 1
        async with factory() as session:
            evidence_id = await session.scalar(
                select(EvidenceItem.id).where(EvidenceItem.raw_item_id == raw_id)
            )
        assert evidence_id is not None
        builder = RichEvidencePacketBuilder(factory)
        first = await builder.build_one(evidence_id)
        second = await builder.build_one(evidence_id)
        assert isinstance(first.current.facts, expected_type)
        assert first == second
        assert first.packet_digest == second.packet_digest
        assert first.operation_key == operation
        assert first.truncation.truncated is False
        serialized = json.dumps(dataclasses.asdict(first), default=str)
        for forbidden in ("raw_payload", "credential", "api_key", "authorization", '"body"'):
            assert forbidden not in serialized.lower()
    finally:
        await _cleanup(factory)
        await engine.dispose()


@pytest.mark.asyncio
async def test_rich_evidence_revision_is_deterministic_and_preserves_numeric_values() -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        raw_id, _, _ = await _seed_ready(factory, "finnhub")
        _, _, _ = await _seed_ready(factory, "finnhub", raw_id=raw_id)
        assert (await EvidenceProjectionHandoffWorker(factory).process_batch(limit=10)).linked == 2
        async with factory() as session:
            evidence_id = await session.scalar(
                select(EvidenceItem.id).where(EvidenceItem.raw_item_id == raw_id)
            )
        assert evidence_id is not None
        packet = await RichEvidencePacketBuilder(factory).build_one(evidence_id)
        assert len(packet.revisions) == 2
        assert len({item.projection_hash for item in packet.revisions}) == 2
        assert all(isinstance(item.facts, FinnhubQuoteFacts) for item in packet.revisions)
        assert {cast(FinnhubQuoteFacts, item.facts).c for item in packet.revisions} == {
            101.25,
            102.5,
        }
    finally:
        await _cleanup(factory)
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("revision_count", [2, 5, 50])
async def test_canonical_evidence_adoption_is_explicit_with_shared_link_time(
    revision_count: int,
) -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        raw_id, _, _ = await _seed_ready(factory, "finnhub")
        for index in range(1, revision_count):
            await _seed_ready(
                factory,
                "finnhub",
                raw_id=raw_id,
                payload_updates={"c": 101.25 + index},
            )
        assert (
            await EvidenceProjectionHandoffWorker(factory).process_batch(limit=100)
        ).linked == revision_count
        async with factory() as session:
            links = tuple(
                await session.scalars(
                    select(EvidenceProjectionLink)
                    .join(EvidenceItem)
                    .where(EvidenceItem.raw_item_id == raw_id)
                )
            )
            evidence_id = links[0].evidence_item_id
        assert sum(link.canonical_evidence for link in links) == 1
        assert len({link.linked_at for link in links}) == 1
        first = await RichEvidencePacketBuilder(factory).build_one(evidence_id)
        second = await RichEvidencePacketBuilder(factory).build_one(evidence_id)
        assert first.packet_digest == second.packet_digest
    finally:
        await _cleanup(factory)
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["marketaux", "finnhub"])
async def test_content_adoption_can_follow_partial_evidence_adoption(provider: str) -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    operation_payload = _extra_operation_payload("company_news") if provider == "finnhub" else None
    try:
        raw_id, _, _ = await _seed_ready(
            factory,
            provider,
            operation_payload=operation_payload,
            payload_updates={"title": None, "canonical_url": None},
        )
        complete_payload = (
            _extra_operation_payload("company_news") if provider == "finnhub" else None
        )
        await _seed_ready(factory, provider, raw_id=raw_id, operation_payload=complete_payload)
        assert (await EvidenceProjectionHandoffWorker(factory).process_batch(limit=10)).linked == 2
        async with factory() as session:
            links = tuple(
                await session.scalars(
                    select(EvidenceProjectionLink)
                    .join(EvidenceItem)
                    .where(EvidenceItem.raw_item_id == raw_id)
                )
            )
            evidence_id = links[0].evidence_item_id
        assert sum(link.canonical_evidence for link in links) == 1
        assert sum(link.canonical_content for link in links) == 1
        assert next(link for link in links if link.canonical_evidence).content_item_id is None
        assert next(link for link in links if link.canonical_content).content_item_id is not None
        packet = await RichEvidencePacketBuilder(factory).build_one(evidence_id)
        assert packet.content.content_item_id is not None
    finally:
        await _cleanup(factory)
        await engine.dispose()


@pytest.mark.asyncio
async def test_company_news_packet_preserves_cross_symbol_revision_context() -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    first = _extra_operation_payload("company_news")
    second = (
        "company_news",
        {**first[1], "symbol": "MSFT"},
    )
    try:
        raw_id, _, _ = await _seed_ready(factory, "finnhub", operation_payload=first)
        await _seed_ready(factory, "finnhub", raw_id=raw_id, operation_payload=second)
        assert (await EvidenceProjectionHandoffWorker(factory).process_batch(limit=10)).linked == 2
        async with factory() as session:
            evidence_id = await session.scalar(
                select(EvidenceItem.id).where(EvidenceItem.raw_item_id == raw_id)
            )
        assert evidence_id is not None
        packet = await RichEvidencePacketBuilder(factory).build_one(evidence_id)
        assert {
            cast(FinnhubCompanyNewsFacts, revision.facts).symbol for revision in packet.revisions
        } == {"AAPL", "MSFT"}
        assert len({revision.projection_hash for revision in packet.revisions}) == 2
    finally:
        await _cleanup(factory)
        await engine.dispose()


@pytest.mark.asyncio
async def test_packet_revision_budget_is_explicit_and_keeps_current() -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        raw_id, _, _ = await _seed_ready(factory, "finnhub")
        await _seed_ready(factory, "finnhub", raw_id=raw_id, payload_updates={"c": 103.5})
        await _seed_ready(factory, "finnhub", raw_id=raw_id, payload_updates={"c": 104.5})
        assert (await EvidenceProjectionHandoffWorker(factory).process_batch(limit=10)).linked == 3
        async with factory() as session:
            evidence_id = await session.scalar(
                select(EvidenceItem.id).where(EvidenceItem.raw_item_id == raw_id)
            )
        assert evidence_id is not None
        packet = await RichEvidencePacketBuilder(factory, max_revisions=2).build_one(evidence_id)
        assert packet.truncation.truncated is True
        assert packet.truncation.reason == "revision_budget"
        assert packet.truncation.total_revision_count == 3
        assert packet.truncation.included_revision_count == 2
        assert packet.current == packet.revisions[-1]
    finally:
        await _cleanup(factory)
        await engine.dispose()


@pytest.mark.asyncio
async def test_sec_recent_history_revision_keeps_canonical_evidence() -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        raw_id, _, payload = await _seed_ready(factory, "sec_edgar")
        revised = {
            **payload,
            "primary_document": "revised8k.htm",
            "official_url": (
                "https://www.sec.gov/Archives/edgar/data/320193/000032019326000001/revised8k.htm"
            ),
            "submissions_file": "CIK0000320193-submissions-001.json",
        }
        await _seed_ready(
            factory,
            "sec_edgar",
            raw_id=raw_id,
            operation_payload=("submissions_recent", revised),
        )
        assert (await EvidenceProjectionHandoffWorker(factory).process_batch(limit=10)).linked == 2
        async with factory() as session:
            evidence_ids = tuple(
                await session.scalars(
                    select(EvidenceItem.id).where(EvidenceItem.raw_item_id == raw_id)
                )
            )
        assert len(evidence_ids) == 1
        packet = await RichEvidencePacketBuilder(factory).build_one(evidence_ids[0])
        assert len(packet.revisions) == 2
        assert {
            cast(SecFilingFacts, revision.facts).submissions_file for revision in packet.revisions
        } == {None, "CIK0000320193-submissions-001.json"}
    finally:
        await _cleanup(factory)
        await engine.dispose()


@pytest.mark.asyncio
async def test_packet_serialized_size_budget_reports_truncation() -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        raw_id, _, _ = await _seed_ready(factory, "marketaux", payload_updates={"title": "A" * 900})
        for letter in ("B", "C", "D", "E"):
            await _seed_ready(
                factory,
                "marketaux",
                raw_id=raw_id,
                payload_updates={"title": letter * 900},
            )
        assert (await EvidenceProjectionHandoffWorker(factory).process_batch(limit=10)).linked == 5
        async with factory() as session:
            evidence_id = await session.scalar(
                select(EvidenceItem.id).where(EvidenceItem.raw_item_id == raw_id)
            )
        assert evidence_id is not None
        packet = await RichEvidencePacketBuilder(
            factory, max_revisions=10, max_serialized_bytes=6_000
        ).build_one(evidence_id)
        assert packet.truncation.truncated is True
        assert packet.truncation.reason == "serialized_size_budget"
        assert packet.current == packet.revisions[-1]
        assert len(canonical_packet_bytes(packet)) <= 6_000
    finally:
        await _cleanup(factory)
        await engine.dispose()


@pytest.mark.asyncio
async def test_single_current_revision_final_utf8_packet_over_budget_fails_closed() -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        raw_id, _, _ = await _seed_ready(
            factory, "marketaux", payload_updates={"title": "事件" * 200}
        )
        assert (await EvidenceProjectionHandoffWorker(factory).process_batch(limit=10)).linked == 1
        async with factory() as session:
            evidence_id = await session.scalar(
                select(EvidenceItem.id).where(EvidenceItem.raw_item_id == raw_id)
            )
        assert evidence_id is not None
        packet = await RichEvidencePacketBuilder(factory, max_serialized_bytes=100_000).build_one(
            evidence_id
        )
        exact_size = len(canonical_packet_bytes(packet))
        assert exact_size > 1_024
        exact = await RichEvidencePacketBuilder(factory, max_serialized_bytes=exact_size).build_one(
            evidence_id
        )
        assert len(canonical_packet_bytes(exact)) == exact_size
        with pytest.raises(RichEvidenceError, match="rich_evidence_packet_budget_exceeded"):
            await RichEvidencePacketBuilder(factory, max_serialized_bytes=exact_size - 1).build_one(
                evidence_id
            )
    finally:
        await _cleanup(factory)
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "assignment",
    [
        "id=gen_random_uuid()",
        "raw_item_id=gen_random_uuid()",
        "observation_id=gen_random_uuid()",
        "provider='marketaux'",
        "operation_key='quote'",
        "projection_schema_version=2",
        "factual_payload=jsonb_set(factual_payload,'{value}','0')",
        "projection_hash=repeat('0',64)",
        "quality_status='complete'",
        "processing_status='pending'",
        "processing_status='processing'",
        "processing_status='blocked'",
        "processing_status='retry'",
        "safe_error_code='unsafe'",
        "attempt_count=attempt_count+1",
        "next_retry_at=now()",
        "processed_at=processed_at + interval '1 second'",
        "created_at=created_at + interval '1 second'",
    ],
)
async def test_linked_projection_direct_sql_factual_mutation_is_rejected(
    assignment: str,
) -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        _, projection_id, _ = await _seed_ready(factory, "eia")
        assert (await EvidenceProjectionHandoffWorker(factory).process_batch(limit=10)).linked == 1
        with pytest.raises(DBAPIError):
            async with factory.begin() as session:
                await session.execute(
                    text(f"UPDATE safe_fact_projections SET {assignment} WHERE id=:id"),
                    {"id": projection_id},
                )
    finally:
        await _cleanup(factory)
        await engine.dispose()


@pytest.mark.asyncio
async def test_packet_builder_rejects_tampered_ready_projection() -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        raw_id, _, _ = await _seed_ready(factory, "marketaux")
        assert (await EvidenceProjectionHandoffWorker(factory).process_batch(limit=10)).linked == 1
        async with factory() as session:
            evidence_id = await session.scalar(
                select(EvidenceItem.id).where(EvidenceItem.raw_item_id == raw_id)
            )
        assert evidence_id is not None
        async with factory.begin() as session:
            raw = await session.get(RawItem, raw_id)
            assert raw is not None
            await session.execute(
                text("UPDATE sources SET access_method='finnhub' WHERE id=:id"),
                {"id": raw.source_id},
            )
        with pytest.raises(RichEvidenceError, match="rich_evidence_provenance_invalid"):
            await RichEvidencePacketBuilder(factory).build_one(evidence_id)
        async with factory.begin() as session:
            raw = await session.get(RawItem, raw_id)
            assert raw is not None
            await session.execute(
                text("UPDATE sources SET access_method='marketaux' WHERE id=:id"),
                {"id": raw.source_id},
            )
    finally:
        await _cleanup(factory)
        await engine.dispose()


@pytest.mark.asyncio
async def test_linked_projection_allows_only_updated_at_and_packet_digest_stays_stable() -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        raw_id, projection_id, _ = await _seed_ready(factory, "finnhub")
        assert (await EvidenceProjectionHandoffWorker(factory).process_batch(limit=10)).linked == 1
        async with factory() as session:
            evidence_id = await session.scalar(
                select(EvidenceItem.id).where(EvidenceItem.raw_item_id == raw_id)
            )
        assert evidence_id is not None
        builder = RichEvidencePacketBuilder(factory)
        before = await builder.build_one(evidence_id)
        async with factory.begin() as session:
            await session.execute(
                text(
                    "UPDATE safe_fact_projections "
                    "SET updated_at=updated_at + interval '1 second' WHERE id=:id"
                ),
                {"id": projection_id},
            )
        after = await builder.build_one(evidence_id)
        assert after.packet_digest == before.packet_digest
        assert after == before
    finally:
        await _cleanup(factory)
        await engine.dispose()


@pytest.mark.asyncio
async def test_linked_projection_delete_is_rejected() -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        _, projection_id, _ = await _seed_ready(factory, "marketaux")
        assert (await EvidenceProjectionHandoffWorker(factory).process_batch(limit=10)).linked == 1
        with pytest.raises(DBAPIError):
            async with factory.begin() as session:
                await session.execute(
                    text("DELETE FROM safe_fact_projections WHERE id=:id"),
                    {"id": projection_id},
                )
    finally:
        await _cleanup(factory)
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "table,assignment",
    [
        ("evidence_projection_links", "attempt_count=attempt_count+1"),
        ("evidence_projection_links", "safe_error_code='changed'"),
        ("evidence_projection_links", "next_retry_at=now()"),
        ("evidence_projection_links", "created_at=created_at + interval '1 second'"),
        ("raw_item_observations", "observed_at=observed_at + interval '1 second'"),
        ("raw_item_observations", "config_revision=1"),
        ("raw_item_observations", "provider_contract_version=2"),
        ("raw_item_observations", "operation_key='changed'"),
        ("raw_items", "collection_run_id=gen_random_uuid()"),
        ("raw_items", "retention_class='changed'"),
        ("evidence_items", "event_time=event_time + interval '1 second'"),
        ("evidence_items", "observed_at=observed_at + interval '1 second'"),
        ("content_items", "body='forbidden'"),
        ("content_items", "source_summary='forbidden'"),
        ("content_items", "title='changed'"),
        ("content_items", "source_published_at=source_published_at + interval '1 second'"),
    ],
)
async def test_linked_lineage_direct_sql_mutation_is_rejected(table: str, assignment: str) -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        provider = "marketaux" if table == "content_items" else "eia"
        raw_id, projection_id, _ = await _seed_ready(factory, provider)
        assert (await EvidenceProjectionHandoffWorker(factory).process_batch(limit=10)).linked == 1
        async with factory() as session:
            projection = await session.get(SafeFactProjection, projection_id)
            link = await session.scalar(
                select(EvidenceProjectionLink).where(
                    EvidenceProjectionLink.safe_fact_projection_id == projection_id
                )
            )
            assert projection is not None and link is not None
            identities = {
                "evidence_projection_links": link.id,
                "raw_item_observations": projection.observation_id,
                "raw_items": raw_id,
                "evidence_items": link.evidence_item_id,
                "content_items": link.content_item_id,
            }
        identity = identities[table]
        assert identity is not None
        with pytest.raises(DBAPIError):
            async with factory.begin() as session:
                await session.execute(
                    text(f"UPDATE {table} SET {assignment} WHERE id=:id"),
                    {"id": identity},
                )
    finally:
        await _cleanup(factory)
        await engine.dispose()


@pytest.mark.asyncio
async def test_link_delete_cannot_remove_packet_or_unlock_projection() -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        raw_id, projection_id, _ = await _seed_ready(factory, "marketaux")
        assert (await EvidenceProjectionHandoffWorker(factory).process_batch(limit=10)).linked == 1
        async with factory() as session:
            link = await session.scalar(
                select(EvidenceProjectionLink).where(
                    EvidenceProjectionLink.safe_fact_projection_id == projection_id
                )
            )
            evidence_id = await session.scalar(
                select(EvidenceItem.id).where(EvidenceItem.raw_item_id == raw_id)
            )
        assert link is not None and evidence_id is not None
        before = await RichEvidencePacketBuilder(factory).build_one(evidence_id)
        with pytest.raises(DBAPIError):
            async with factory.begin() as session:
                await session.execute(
                    text("DELETE FROM evidence_projection_links WHERE id=:id"), {"id": link.id}
                )
        after = await RichEvidencePacketBuilder(factory).build_one(evidence_id)
        assert after.packet_digest == before.packet_digest
        with pytest.raises(DBAPIError):
            async with factory.begin() as session:
                await session.execute(
                    text("UPDATE safe_fact_projections SET attempt_count=99 WHERE id=:id"),
                    {"id": projection_id},
                )
    finally:
        await _cleanup(factory)
        await engine.dispose()


@pytest.mark.asyncio
async def test_nonlinked_association_can_be_cleaned_up() -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        _, projection_id, _ = await _seed_ready(factory, "eia")
        async with factory.begin() as session:
            identity = await session.scalar(
                text(
                    "INSERT INTO evidence_projection_links"
                    "(safe_fact_projection_id,status,attempt_count) "
                    "VALUES (:projection,'pending',0) RETURNING id"
                ),
                {"projection": projection_id},
            )
            await session.execute(
                text("DELETE FROM evidence_projection_links WHERE id=:id"), {"id": identity}
            )
        async with factory() as session:
            assert await session.get(EvidenceProjectionLink, identity) is None
    finally:
        await _cleanup(factory)
        await engine.dispose()


@pytest.mark.asyncio
async def test_packet_retention_and_canonical_time_tampering_fail_closed() -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        raw_id, _, _ = await _seed_ready(factory, "eia")
        assert (await EvidenceProjectionHandoffWorker(factory).process_batch(limit=10)).linked == 1
        async with factory() as session:
            evidence_id = await session.scalar(
                select(EvidenceItem.id).where(EvidenceItem.raw_item_id == raw_id)
            )
        assert evidence_id is not None
        async with factory() as session:
            raw = await session.get(RawItem, raw_id)
            assert raw is not None
            source_id = raw.source_id
        with pytest.raises(DBAPIError):
            async with factory.begin() as session:
                await session.execute(
                    text("UPDATE raw_items SET retention_class='link_only' WHERE id=:id"),
                    {"id": raw_id},
                )
        with pytest.raises(DBAPIError):
            async with factory.begin() as session:
                await session.execute(
                    text("UPDATE sources SET retention_class='link_only' WHERE id=:id"),
                    {"id": source_id},
                )
        with pytest.raises(DBAPIError):
            async with factory.begin() as session:
                await session.execute(
                    text(
                        "UPDATE evidence_items SET event_time=event_time + interval '1 day' "
                        "WHERE id=:id"
                    ),
                    {"id": evidence_id},
                )
    finally:
        await _cleanup(factory)
        await engine.dispose()


@pytest.mark.asyncio
async def test_packet_scan_budget_crosses_sparse_quality_rows_without_starvation() -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        for index in range(8):
            _, projection_id, _ = await _seed_ready(
                factory, "marketaux", payload_updates={"title": None}
            )
            await _link_with_explicit_evidence_id(
                factory, projection_id, uuid.UUID(int=10_000 + index)
            )
        _, complete_projection, _ = await _seed_ready(
            factory,
            "eia",
            payload_updates={"unit": "dollars_per_megawatthour"},
        )
        await _link_with_explicit_evidence_id(factory, complete_projection, uuid.UUID(int=20_000))
        query_count = 0

        def count_query(*_args: object) -> None:
            nonlocal query_count
            query_count += 1

        event.listen(engine.sync_engine, "before_cursor_execute", count_query)
        try:
            page = await RichEvidencePacketBuilder(factory).list_packets(
                after_evidence_id=uuid.UUID(int=9_999),
                limit=1,
                quality="complete",
                scan_limit=20,
            )
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", count_query)
        assert page.returned_count == 1
        assert page.scanned_count == 9
        assert page.packets[0].quality == "complete"
        assert page.scan_exhausted is False
        assert query_count <= packet_query_budget(20)
    finally:
        await _cleanup(factory)
        await engine.dispose()


@pytest.mark.parametrize("scan_limit,maximum", [(1, 8), (50, 8), (500, 36)])
def test_packet_prefetch_query_budget_is_bounded(scan_limit: int, maximum: int) -> None:
    assert packet_query_budget(scan_limit) == maximum


@pytest.mark.asyncio
async def test_packet_prefetch_query_count_is_measured_at_real_scale() -> None:
    """Measure statements over real linked rows, including the 100-row boundary."""
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        for index in range(500):
            _, projection_id, _ = await _seed_ready(factory, "marketaux")
            await _link_with_explicit_evidence_id(
                factory, projection_id, uuid.UUID(int=100_000 + index)
            )
        builder = RichEvidencePacketBuilder(factory, max_batch_size=500)
        for size in (1, 50, 100, 101, 500):
            query_count = 0

            def count_query(*_args: object) -> None:
                nonlocal query_count
                query_count += 1

            event.listen(engine.sync_engine, "before_cursor_execute", count_query)
            try:
                page = await builder.list_packets(
                    after_evidence_id=uuid.UUID(int=99_999),
                    limit=size,
                    scan_limit=size,
                )
            finally:
                event.remove(engine.sync_engine, "before_cursor_execute", count_query)
            assert page.returned_count == size
            assert page.scanned_count == size
            assert len({packet.evidence_id for packet in page.packets}) == size
            assert query_count <= packet_query_budget(size)
            assert page.has_more is (size < 500)
            if size == 500:
                assert page.next_evidence_id is None
            else:
                assert page.next_evidence_id is not None
        one = await builder.build_one(uuid.UUID(int=100_000))
        assert (
            one.packet_digest
            == (
                await builder.list_packets(
                    after_evidence_id=uuid.UUID(int=99_999), limit=1, scan_limit=1
                )
            )
            .packets[0]
            .packet_digest
        )
    finally:
        await _cleanup(factory)
        await engine.dispose()


@pytest.mark.asyncio
async def test_0010_migration_roundtrip_and_linked_state_guard() -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    revision = ScriptDirectory.from_config(Config("alembic.ini")).get_revision("0010")
    assert revision.down_revision == "0009"

    def roundtrip(connection: object) -> None:
        with Operations.context(MigrationContext.configure(connection)):
            revision.module.downgrade()
            revision.module.upgrade()

    async with engine.connect() as connection:
        transaction = await connection.begin()
        await connection.run_sync(roundtrip)
        await transaction.rollback()
    await engine.dispose()


@pytest.mark.asyncio
async def test_0010_controlled_gate_blocks_bare_state_and_applies_exact_upgrade() -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    revision = ScriptDirectory.from_config(Config("alembic.ini")).get_revision("0010")

    def downgrade(connection: object) -> None:
        with Operations.context(MigrationContext.configure(connection)):
            revision.module.downgrade()

    try:
        async with engine.begin() as connection:
            await connection.run_sync(downgrade)
            await connection.execute(text("UPDATE alembic_version SET version_num='0009'"))
        blocked = await controlled_upgrade_0010(engine, execute=False, writers_stopped=False)
        assert blocked.status == "BLOCKED"
        assert blocked.safe_errors == ("migration_0010_writers_not_stopped",)
        dry_run = await controlled_upgrade_0010(engine, execute=False, writers_stopped=True)
        assert dry_run.status == "DRY_RUN"
        assert dry_run.database_revision == "0009"
        applied = await controlled_upgrade_0010(
            engine,
            execute=True,
            writers_stopped=True,
        )
        assert applied.status == "PASS"
        assert applied.database_revision == "0010"
        assert applied.migration_executed is True
        assert applied.safe_errors == ()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_0010_upgrade_accepts_existing_finnhub_company_news_lineage() -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    revision = ScriptDirectory.from_config(Config("alembic.ini")).get_revision("0010")

    def downgrade(connection: object) -> None:
        with Operations.context(MigrationContext.configure(connection)):
            revision.module.downgrade()

    def upgrade(connection: object) -> None:
        with Operations.context(MigrationContext.configure(connection)):
            revision.module.upgrade()

    try:
        await _cleanup(factory)
        async with engine.begin() as connection:
            await connection.run_sync(downgrade)
        raw_id, projection_id, _ = await _seed_ready(
            factory,
            "finnhub",
            operation_payload=_extra_operation_payload("company_news"),
        )
        async with factory.begin() as session:
            projection = await session.get(SafeFactProjection, projection_id)
            assert projection is not None
            observation = await session.get(RawItemObservation, projection.observation_id)
            raw = await session.get(RawItem, raw_id)
            assert observation is not None and raw is not None
            content = await handoff_module._content(session, projection, raw)
            evidence = await handoff_module._evidence(
                session, projection, observation, raw, content
            )
            assert content is not None
            await session.execute(
                text("""
                INSERT INTO evidence_projection_links(
                  safe_fact_projection_id,evidence_item_id,content_item_id,status,
                  attempt_count,linked_at
                ) VALUES (:projection,:evidence,:content,'linked',1,:linked_at)
                """),
                {
                    "projection": projection.id,
                    "evidence": evidence.id,
                    "content": content.id,
                    "linked_at": datetime.now(UTC),
                },
            )
        report, exit_code = await validate_0010_pre_migration(engine)
        assert exit_code == 0
        assert report == {
            "status": "PASS",
            "checked_linked_projection_count": 1,
            "safe_errors": [],
        }
        async with factory.begin() as session:
            await session.execute(
                text("UPDATE safe_fact_projections SET quality_status='partial' WHERE id=:id"),
                {"id": projection_id},
            )
        blocked, blocked_code = await validate_0010_pre_migration(engine)
        assert blocked_code == 2
        assert blocked["status"] == "BLOCKED"
        assert blocked["safe_errors"] == ["migration_0010_projection_contract_invalid"]
        async with factory.begin() as session:
            projection = await session.get(SafeFactProjection, projection_id)
            assert projection is not None
            projection.quality_status = "complete"
        async with engine.begin() as connection:
            await connection.run_sync(upgrade)
        async with factory() as session:
            link = await session.scalar(
                select(EvidenceProjectionLink).where(
                    EvidenceProjectionLink.safe_fact_projection_id == projection_id
                )
            )
            assert link is not None
            assert link.canonical_evidence is True
            assert link.canonical_content is True
    finally:
        await _cleanup(factory)
        await engine.dispose()
