# ruff: noqa: E501

from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, func, select, text
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
from market_intelligence.safe_projection.contracts import canonical_projection_hash

POSTGRES_TEST_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://market_intelligence:local_dev_only@localhost:5432/market_intelligence",
)


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
) -> tuple[uuid.UUID, uuid.UUID, dict[str, object]]:
    marker = uuid.uuid4().hex
    operation, payload = _payload(provider, marker)
    if raw_id is not None and provider == "finnhub":
        payload["c"] = 102.5
    if raw_id is not None and provider == "marketaux":
        async with factory() as lookup:
            raw = await lookup.get(RawItem, raw_id)
            assert raw is not None and raw.external_id is not None
            payload["provider_item_id"] = raw.external_id
    if payload_updates:
        payload.update(payload_updates)
    projection_hash = canonical_projection_hash(payload)
    async with factory.begin() as session:
        if raw_id is None:
            source_id = await session.scalar(
                text("""
                INSERT INTO sources(code,name,source_type,access_method,authorization_status,retention_class,enabled)
                VALUES (:code,'R8A synthetic','api',:provider,'authorized','metadata_only',true) RETURNING id
            """),
                {"code": f"r8a-{marker}", "provider": provider},
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
                VALUES (:source,:account,:run,:external,:now,200,'application/json',:location,:hash,'metadata_only','pending') RETURNING id
            """),
                {
                    "source": source_id,
                    "account": account_id,
                    "run": run_id,
                    "external": str(payload["provider_item_id"]),
                    "now": datetime.now(UTC),
                    "location": f"internal://r8a/{marker}",
                    "hash": marker.ljust(64, "0")[:64],
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
        observation_id = await session.scalar(
            text("""
            INSERT INTO raw_item_observations(collection_run_id,raw_item_id,source_id,source_account_id,provider,operation_key,provider_contract_version,observed_at,projection_hash,observation_kind)
            VALUES (:run,:raw,:source,:account,:provider,:operation,1,:now,:hash,'revision_candidate') RETURNING id
        """),
            {
                "run": run_id,
                "raw": raw_id,
                "source": source_id,
                "account": account_id,
                "provider": provider,
                "operation": operation,
                "now": datetime.now(UTC),
                "hash": projection_hash,
            },
        )
        projection_id = await session.scalar(
            text("""
            INSERT INTO safe_fact_projections(observation_id,raw_item_id,provider,operation_key,projection_schema_version,factual_payload,projection_hash,quality_status,processing_status,processed_at)
            VALUES (:observation,:raw,:provider,:operation,1,CAST(:payload AS jsonb),:hash,'complete','ready',:now) RETURNING id
        """),
            {
                "observation": observation_id,
                "raw": raw_id,
                "provider": provider,
                "operation": operation,
                "payload": __import__("json").dumps(payload),
                "hash": projection_hash,
                "now": datetime.now(UTC),
            },
        )
    assert raw_id and projection_id
    return raw_id, projection_id, payload


async def _cleanup(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory.begin() as session:
        source_ids = tuple(
            await session.scalars(
                select(RawItem.source_id)
                .join(SafeFactProjection, SafeFactProjection.raw_item_id == RawItem.id)
                .where(
                    SafeFactProjection.provider.in_(("marketaux", "finnhub", "eia", "sec_edgar")),
                    RawItem.payload_location.like("internal://r8a/%"),
                )
            )
        )
        if not source_ids:
            return
        raw_ids = tuple(
            await session.scalars(select(RawItem.id).where(RawItem.source_id.in_(source_ids)))
        )
        projection_ids = tuple(
            await session.scalars(
                select(SafeFactProjection.id).where(SafeFactProjection.raw_item_id.in_(raw_ids))
            )
        )
        await session.execute(
            delete(EvidenceProjectionLink).where(
                EvidenceProjectionLink.safe_fact_projection_id.in_(projection_ids)
            )
        )
        await session.execute(delete(EvidenceItem).where(EvidenceItem.raw_item_id.in_(raw_ids)))
        await session.execute(delete(ContentItem).where(ContentItem.raw_item_id.in_(raw_ids)))
        await session.execute(
            delete(SafeFactProjection).where(SafeFactProjection.id.in_(projection_ids))
        )
        await session.execute(
            delete(RawItemObservation).where(RawItemObservation.raw_item_id.in_(raw_ids))
        )
        account_ids = tuple(
            await session.scalars(select(RawItem.source_account_id).where(RawItem.id.in_(raw_ids)))
        )
        await session.execute(delete(RawItem).where(RawItem.id.in_(raw_ids)))
        await session.execute(
            text("DELETE FROM collection_runs WHERE source_id = ANY(:ids)"),
            {"ids": list(source_ids)},
        )
        await session.execute(
            text("DELETE FROM source_accounts WHERE id = ANY(:ids)"), {"ids": list(account_ids)}
        )
        await session.execute(
            text("DELETE FROM sources WHERE id = ANY(:ids)"), {"ids": list(source_ids)}
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


def _legacy_envelope(provider: str, payload: dict[str, object]) -> object:
    context: dict[str, object] = {"observed_at": datetime.now(UTC)}
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
            assert raw is not None
            outcome = await EvidenceWriteService(session).write_one(
                EvidenceWriteRequest(
                    envelope=_legacy_envelope(provider, payload),  # type: ignore[arg-type]
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
        title="Synthetic",
        source_summary=None,
        body=None,
        body_availability=availability,
        author=None,
        language=None,
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
        metadata_={},
    )
    session.add(item)
    await session.flush()
    return item


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
                session, raw, payload, kind=kind, availability=availability, url=url
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
                    text("""
                    INSERT INTO evidence_projection_links(
                      safe_fact_projection_id,evidence_item_id,content_item_id,status,linked_at
                    ) VALUES (:projection,:evidence,:content,'linked',:now)
                    """),
                    {
                        "projection": projection_id,
                        "evidence": evidence_id,
                        "content": content_id,
                        "now": datetime.now(UTC),
                    },
                )
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
