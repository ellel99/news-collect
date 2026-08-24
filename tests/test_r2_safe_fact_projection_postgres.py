from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from market_intelligence.collection.contracts import RawItemEnvelope
from market_intelligence.collection.downstream import persist_fetch_result
from market_intelligence.db.models import (
    CollectionRun,
    CollectionRunStatus,
    RawItem,
    RawItemObservation,
    RawItemObservationKind,
    SafeFactProjection,
    SafeProjectionProcessingStatus,
)
from market_intelligence.providers.contracts import ProviderFetchResult
from market_intelligence.safe_projection.contracts import ProjectionContractError
from market_intelligence.safe_projection.worker import SafeFactProjectionWorker

POSTGRES_TEST_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://market_intelligence:local_dev_only@localhost:5432/market_intelligence",
)


async def _seed_target(factory: async_sessionmaker[AsyncSession]) -> dict[str, uuid.UUID]:
    marker = uuid.uuid4().hex
    async with factory.begin() as session:
        source_id = await session.scalar(
            text("""
            INSERT INTO sources(
              code,name,source_type,access_method,authorization_status,retention_class,enabled
            ) VALUES (:code,'R2 safe facts','api','marketaux','authorized','metadata_only',true)
            RETURNING id
            """),
            {"code": f"r2-{marker}"},
        )
        account_id = await session.scalar(
            text("""
            INSERT INTO source_accounts(source_id,identity_status,enabled,collection_options)
            VALUES (:source,'verified',true,'{"query":"technology"}'::jsonb) RETURNING id
            """),
            {"source": source_id},
        )
        target_id = await session.scalar(
            text("""
            INSERT INTO collection_targets(
              target_key,source_id,source_account_id,operation_key,legacy_cursor_type,
              operation_config_version,provider_contract_version,operation_config,status,
              cadence_seconds,batch_limit,max_response_bytes,request_timeout_seconds,
              max_runtime_seconds,cursor_strategy,collection_mode,backfill_policy,
              revision_policy,rate_limit_group,next_due_at,health_status
            ) VALUES (
              :key,:source,:account,'news_all','provider_cursor_v1',1,1,
              '{"query":"technology"}'::jsonb,'paused',300,3,1000000,10,60,
              'compound','incremental','disabled','ignore','marketaux:default',:now,'unknown'
            ) RETURNING id
            """),
            {
                "key": f"r2.{marker}",
                "source": source_id,
                "account": account_id,
                "now": datetime.now(UTC),
            },
        )
    assert source_id and account_id and target_id
    return {"source": source_id, "account": account_id, "target": target_id}


async def _new_run(
    factory: async_sessionmaker[AsyncSession],
    ids: dict[str, uuid.UUID],
    *,
    run_mode: str = "normal",
) -> uuid.UUID:
    async with factory.begin() as session:
        identity = await session.scalar(
            text("""
            INSERT INTO collection_runs(
              target_id,run_mode,dispatch_identity,source_id,source_account_id,started_at,status
            ) VALUES (:target,:mode,:dispatch,:source,:account,:now,'running') RETURNING id
            """),
            {
                **ids,
                "dispatch": uuid.uuid4().hex,
                "now": datetime.now(UTC),
                "mode": run_mode,
            },
        )
    assert identity
    return identity


def _result(marker: str, *, title: str = "Safe factual title") -> ProviderFetchResult:
    payload = {
        "provider_item_id": marker,
        "published_at": "2026-01-01T00:00:00+00:00",
        "title": title,
        "canonical_url": f"https://example.com/{marker}",
        "source_identity": "Example",
        "query": "technology",
        "language": "en",
        "symbols": ["NVDA"],
        "description_coverage": "blocked",
        "snippet_coverage": "blocked",
    }
    return ProviderFetchResult(
        raw_items=(
            RawItemEnvelope(
                external_id=marker,
                fetched_at=datetime.now(UTC),
                http_status=200,
                content_type="application/json",
                payload_location=f"internal://provider/marketaux/{marker}",
                payload_hash=marker.ljust(64, "0")[:64],
                retention_class="metadata_only",
            ),
        ),
        sanitized_metadata=(
            {
                "provider_item_id": marker,
                "published_at": payload["published_at"],
                "field_names": ["title"],
                "has_title": True,
                "has_description": False,
                "has_snippet": False,
                "has_source_url": True,
            },
        ),
        factual_projections=(payload,),
        next_cursor=None,
        has_more=False,
        safe_errors=(),
        provider="marketaux",
        contract_version=1,
    )


async def _persist(
    factory: async_sessionmaker[AsyncSession],
    ids: dict[str, uuid.UUID],
    run_id: uuid.UUID,
    result: ProviderFetchResult,
) -> None:
    async with factory.begin() as session:
        await persist_fetch_result(
            session,
            run_id=run_id,
            source_id=ids["source"],
            source_account_id=ids["account"],
            provider="marketaux",
            target_id=ids["target"],
            operation_key="news_all",
            config_revision=1,
            provider_contract_version=1,
            result=result,
        )
        run = await session.get(CollectionRun, run_id)
        assert run is not None
        run.status = CollectionRunStatus.SUCCEEDED
        run.finished_at = datetime.now(UTC)


async def _cleanup(factory: async_sessionmaker[AsyncSession], ids: dict[str, uuid.UUID]) -> None:
    async with factory.begin() as session:
        raw_ids = select(RawItem.id).where(RawItem.source_id == ids["source"])
        await session.execute(
            delete(SafeFactProjection).where(SafeFactProjection.raw_item_id.in_(raw_ids))
        )
        await session.execute(
            delete(RawItemObservation).where(RawItemObservation.source_id == ids["source"])
        )
        await session.execute(delete(RawItem).where(RawItem.source_id == ids["source"]))
        await session.execute(delete(CollectionRun).where(CollectionRun.source_id == ids["source"]))
        await session.execute(
            text("DELETE FROM collection_targets WHERE id=:target"), {"target": ids["target"]}
        )
        await session.execute(
            text("DELETE FROM source_accounts WHERE id=:account"), {"account": ids["account"]}
        )
        await session.execute(
            text("DELETE FROM sources WHERE id=:source"), {"source": ids["source"]}
        )


@pytest.mark.asyncio
async def test_observation_classification_idempotency_and_worker_recovery() -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ids = await _seed_target(factory)
    marker = uuid.uuid4().hex
    try:
        run1 = await _new_run(factory, ids)
        await _persist(factory, ids, run1, _result(marker))
        run2 = await _new_run(factory, ids)
        await _persist(factory, ids, run2, _result(marker))
        run3 = await _new_run(factory, ids)
        await _persist(factory, ids, run3, _result(marker, title="Revised safe title"))

        async with factory() as session:
            observations = tuple(
                await session.scalars(
                    select(RawItemObservation).where(RawItemObservation.source_id == ids["source"])
                )
            )
            kinds = {item.collection_run_id: item.observation_kind for item in observations}
            assert kinds == {
                run1: RawItemObservationKind.FIRST_SEEN,
                run2: RawItemObservationKind.DUPLICATE_SAME_PROJECTION,
                run3: RawItemObservationKind.REVISION_CANDIDATE,
            }
            assert len({item.raw_item_id for item in observations}) == 1
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(SafeFactProjection)
                    .where(SafeFactProjection.raw_item_id == observations[0].raw_item_id)
                )
                == 3
            )

        report = await SafeFactProjectionWorker(factory).process_batch(limit=10)
        assert (report.claimed, report.ready, report.blocked) == (3, 3, 0)
        assert (await SafeFactProjectionWorker(factory).process_batch(limit=10)).claimed == 0

        async with factory.begin() as session:
            projection = await session.scalar(
                select(SafeFactProjection)
                .join(
                    RawItemObservation,
                    RawItemObservation.id == SafeFactProjection.observation_id,
                )
                .where(RawItemObservation.collection_run_id == run3)
                .with_for_update()
            )
            assert projection is not None
            projection.processing_status = SafeProjectionProcessingStatus.VALIDATING
            projection.updated_at = datetime.now(UTC) - timedelta(hours=1)
            projection.attempt_count = 1
        recovered = await SafeFactProjectionWorker(
            factory, stale_after=timedelta(minutes=1)
        ).process_batch(limit=10)
        assert (recovered.recovered, recovered.ready) == (1, 1)
    finally:
        await _cleanup(factory, ids)
        await engine.dispose()


@pytest.mark.asyncio
async def test_invalid_payload_rolls_back_and_db_provenance_fails_closed() -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ids = await _seed_target(factory)
    marker = uuid.uuid4().hex
    run_id = await _new_run(factory, ids)
    try:
        with pytest.raises(ProjectionContractError, match="projection_secret_marker_detected"):
            await _persist(factory, ids, run_id, _result(marker, title="token=unsafe"))
        async with factory() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(RawItem)
                    .where(RawItem.source_id == ids["source"])
                )
                == 0
            )

        await _persist(factory, ids, run_id, _result(marker))
        async with factory.begin() as session:
            raw_id = await session.scalar(
                select(RawItem.id).where(RawItem.source_id == ids["source"])
            )
            with pytest.raises(DBAPIError, match="raw_item_observation_provenance_mismatch"):
                async with session.begin_nested():
                    await session.execute(
                        text("""
                    INSERT INTO raw_item_observations(
                      collection_run_id,raw_item_id,target_id,source_id,source_account_id,
                      provider,operation_key,config_revision,provider_contract_version,
                      observed_at,projection_hash,observation_kind
                    ) VALUES (
                      :run,:raw,:target,:wrong,:account,'marketaux','news_all',1,1,
                      :now,:hash,'duplicate_same_projection'
                    )
                        """),
                        {
                            "run": run_id,
                            "raw": raw_id,
                            "target": ids["target"],
                            "wrong": uuid.uuid4(),
                            "account": ids["account"],
                            "now": datetime.now(UTC),
                            "hash": "a" * 64,
                        },
                    )
    finally:
        await _cleanup(factory, ids)
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_duplicate_observations_keep_one_canonical_raw_item() -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ids = await _seed_target(factory)
    marker = uuid.uuid4().hex
    try:
        runs = (
            await _new_run(factory, ids),
            await _new_run(factory, ids, run_mode="backfill"),
        )
        await asyncio.gather(
            _persist(factory, ids, runs[0], _result(marker)),
            _persist(factory, ids, runs[1], _result(marker)),
        )
        async with factory() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(RawItem)
                    .where(RawItem.source_id == ids["source"])
                )
                == 1
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(RawItemObservation)
                    .where(RawItemObservation.source_id == ids["source"])
                )
                == 2
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(SafeFactProjection)
                    .join(
                        RawItemObservation,
                        RawItemObservation.id == SafeFactProjection.observation_id,
                    )
                    .where(RawItemObservation.source_id == ids["source"])
                )
                == 2
            )
    finally:
        await _cleanup(factory, ids)
        await engine.dispose()


@pytest.mark.asyncio
async def test_worker_blocks_persisted_unsafe_payload_without_exposing_value() -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ids = await _seed_target(factory)
    marker = uuid.uuid4().hex
    try:
        run_id = await _new_run(factory, ids)
        await _persist(factory, ids, run_id, _result(marker))
        async with factory.begin() as session:
            projection = await session.scalar(
                select(SafeFactProjection)
                .join(
                    RawItemObservation,
                    RawItemObservation.id == SafeFactProjection.observation_id,
                )
                .where(RawItemObservation.collection_run_id == run_id)
                .with_for_update()
            )
            assert projection is not None
            projection.factual_payload = {
                **projection.factual_payload,
                "title": "authorization=must-not-escape",
            }

        report = await SafeFactProjectionWorker(factory).process_batch(limit=1)
        assert (report.claimed, report.ready, report.blocked) == (1, 0, 1)
        async with factory() as session:
            projection = await session.scalar(
                select(SafeFactProjection)
                .join(
                    RawItemObservation,
                    RawItemObservation.id == SafeFactProjection.observation_id,
                )
                .where(RawItemObservation.collection_run_id == run_id)
            )
            assert projection is not None
            assert projection.processing_status is SafeProjectionProcessingStatus.BLOCKED
            assert projection.safe_error_code == "projection_secret_marker_detected"
            assert "must-not-escape" not in projection.safe_error_code
    finally:
        await _cleanup(factory, ids)
        await engine.dispose()
