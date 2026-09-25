"""Synthetic PostgreSQL lineage helpers for M2-C tests."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from market_intelligence.db.models import RawItem
from market_intelligence.safe_projection.contracts import (
    canonical_projection_hash,
    normalize_and_classify_factual_payload,
)


def payload(provider: str, marker: str) -> tuple[str, dict[str, object]]:
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
        "official_url": (
            "https://www.sec.gov/Archives/edgar/data/320193/000032019326000001/a8k.htm"
        ),
        "official_source": True,
    }


async def seed_ready(
    factory: async_sessionmaker[AsyncSession],
    provider: str,
    *,
    raw_id: uuid.UUID | None = None,
    payload_updates: dict[str, object] | None = None,
) -> tuple[uuid.UUID, uuid.UUID, dict[str, object]]:
    marker = uuid.uuid4().hex
    operation, material = payload(provider, marker)
    if raw_id is not None and provider == "marketaux":
        async with factory() as lookup:
            raw = await lookup.get(RawItem, raw_id)
            assert raw is not None and raw.external_id is not None
            material["provider_item_id"] = raw.external_id
    if payload_updates:
        material.update(payload_updates)
    material, quality = normalize_and_classify_factual_payload(provider, operation, 1, material)
    projection_hash = canonical_projection_hash(material)
    async with factory.begin() as session:
        if raw_id is None:
            retention = "link_only" if provider in {"marketaux", "sec_edgar"} else "metadata_only"
            source_id = await session.scalar(
                text("""
                INSERT INTO sources(
                  code,name,source_type,access_method,authorization_status,
                  retention_class,enabled
                ) VALUES (
                  :code,'M2C synthetic','api',:provider,'authorized',:retention,true
                ) RETURNING id
                """),
                {"code": f"m2c-{marker}", "provider": provider, "retention": retention},
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
                INSERT INTO collection_runs(
                  source_id,source_account_id,started_at,finished_at,status
                ) VALUES (:source,:account,:now,:now,'succeeded') RETURNING id
                """),
                {"source": source_id, "account": account_id, "now": datetime.now(UTC)},
            )
            raw_id = await session.scalar(
                text("""
                INSERT INTO raw_items(
                  source_id,source_account_id,collection_run_id,external_id,fetched_at,
                  http_status,content_type,payload_location,payload_hash,retention_class,
                  parse_status
                ) VALUES (
                  :source,:account,:run,:external,:now,200,'application/json',
                  :location,:hash,:retention,'pending'
                ) RETURNING id
                """),
                {
                    "source": source_id,
                    "account": account_id,
                    "run": run_id,
                    "external": str(material["provider_item_id"]),
                    "now": datetime.now(UTC),
                    "location": f"internal://m2c/{marker}",
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
                INSERT INTO collection_runs(
                  source_id,source_account_id,started_at,finished_at,status
                ) VALUES (:source,:account,:now,:now,'succeeded') RETURNING id
                """),
                {"source": source_id, "account": account_id, "now": datetime.now(UTC)},
            )
        observation_id = await session.scalar(
            text("""
            INSERT INTO raw_item_observations(
              collection_run_id,raw_item_id,source_id,source_account_id,provider,
              operation_key,provider_contract_version,observed_at,projection_hash,
              observation_kind
            ) VALUES (
              :run,:raw,:source,:account,:provider,:operation,1,:now,:hash,
              'revision_candidate'
            ) RETURNING id
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
            INSERT INTO safe_fact_projections(
              observation_id,raw_item_id,provider,operation_key,projection_schema_version,
              factual_payload,projection_hash,quality_status,processing_status,processed_at
            ) VALUES (
              :observation,:raw,:provider,:operation,1,CAST(:payload AS jsonb),:hash,
              :quality,'ready',:now
            ) RETURNING id
            """),
            {
                "observation": observation_id,
                "raw": raw_id,
                "provider": provider,
                "operation": operation,
                "payload": json.dumps(material),
                "hash": projection_hash,
                "quality": quality,
                "now": datetime.now(UTC),
            },
        )
    assert raw_id is not None and projection_id is not None
    return raw_id, projection_id, material


async def cleanup(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory.begin() as session:
        await session.execute(text("TRUNCATE TABLE event_candidates CASCADE"))
        await session.execute(text("TRUNCATE TABLE sources CASCADE"))
