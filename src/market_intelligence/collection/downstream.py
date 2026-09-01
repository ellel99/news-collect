"""Atomic canonical RawItem and R2 durable safe projection persistence."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from market_intelligence.db.models import (
    ParseStatus,
    RawItem,
    RawItemObservation,
    RawItemObservationKind,
    SafeFactProjection,
    SafeProjectionProcessingStatus,
    SafeProjectionQualityStatus,
)
from market_intelligence.providers.contracts import ProviderFetchResult
from market_intelligence.safe_projection.contracts import (
    canonical_projection_hash,
    normalize_and_classify_factual_payload,
)


class DownstreamPersistenceError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class DownstreamCounts:
    fetched: int
    new: int
    duplicates: int
    observations: int = 0
    projections: int = 0


async def persist_fetch_result(
    session: AsyncSession,
    *,
    run_id: UUID,
    source_id: UUID,
    source_account_id: UUID | None,
    provider: str,
    target_id: UUID | None,
    operation_key: str,
    config_revision: int | None,
    provider_contract_version: int,
    result: ProviderFetchResult,
    observation_key: str = "run",
) -> DownstreamCounts:
    """Atomically persist RawItem + observation + PENDING safe factual projection."""

    new_count = duplicate_count = 0
    observation_count = projection_count = 0
    if len(result.factual_projections) != len(result.raw_items):
        raise DownstreamPersistenceError("safe_fact_projection_missing")
    for envelope, metadata in zip(result.raw_items, result.factual_projections, strict=True):
        payload, quality = normalize_and_classify_factual_payload(
            provider, operation_key, 1, _factual_payload(provider, operation_key, metadata)
        )
        projection_hash = canonical_projection_hash(payload)
        raw_id, inserted = await _raw_item(session, run_id, source_id, source_account_id, envelope)
        if inserted:
            new_count += 1
        else:
            duplicate_count += 1
        observation_id = await _observation(
            session,
            run_id=run_id,
            raw_item_id=raw_id,
            target_id=target_id,
            source_id=source_id,
            source_account_id=source_account_id,
            provider=provider,
            operation_key=operation_key,
            config_revision=config_revision,
            provider_contract_version=provider_contract_version,
            observed_at=envelope.fetched_at,
            projection_hash=projection_hash,
            inserted=inserted,
            observation_key=observation_key,
        )
        observation_count += 1
        projection_count += await _projection(
            session,
            observation_id=observation_id,
            raw_item_id=raw_id,
            provider=provider,
            operation_key=operation_key,
            payload=payload,
            projection_hash=projection_hash,
            quality=quality,
        )
    return DownstreamCounts(
        len(result.raw_items),
        new_count,
        duplicate_count,
        observation_count,
        projection_count,
    )


def _factual_payload(provider: str, operation_key: str, metadata: Any) -> dict[str, Any]:
    if (provider, operation_key) in {
        ("finnhub", "company_news"),
        ("eia", "electricity_rto_region_data"),
    }:
        if not isinstance(metadata, Mapping):
            raise DownstreamPersistenceError("safe_fact_projection_contract_unknown")
        return dict(metadata)
    fields = {
        ("marketaux", "news_all"): (
            "provider_item_id",
            "published_at",
            "title",
            "canonical_url",
            "source_identity",
            "query",
            "language",
            "symbols",
            "description_coverage",
            "snippet_coverage",
        ),
        ("finnhub", "quote"): (
            "provider_item_id",
            "published_at",
            "symbol",
            "provider_timestamp",
            "c",
            "d",
            "dp",
            "h",
            "l",
            "o",
            "pc",
            "currency",
            "exchange",
        ),
        ("eia", "electricity_retail_sales"): (
            "provider_item_id",
            "published_at",
            "period",
            "dataset",
            "series_identity",
            "geography",
            "sector",
            "metric",
            "value",
            "unit",
        ),
        ("sec_edgar", "submissions_recent"): (
            "provider_item_id",
            "published_at",
            "cik",
            "ticker",
            "accession_number",
            "filing_date",
            "form",
            "primary_document",
            "official_url",
            "official_source",
        ),
    }.get((provider, operation_key))
    if fields is None or not isinstance(metadata, Mapping):
        raise DownstreamPersistenceError("safe_fact_projection_contract_unknown")
    result = {field: metadata.get(field) for field in fields}
    if provider == "sec_edgar" and "submissions_file" in metadata:
        result["submissions_file"] = metadata["submissions_file"]
    return result


async def _observation(
    session: AsyncSession,
    *,
    run_id: UUID,
    raw_item_id: UUID,
    target_id: UUID | None,
    source_id: UUID,
    source_account_id: UUID | None,
    provider: str,
    operation_key: str,
    config_revision: int | None,
    provider_contract_version: int,
    observed_at: Any,
    projection_hash: str,
    inserted: bool,
    observation_key: str,
) -> UUID:
    previous_hash = await session.scalar(
        select(RawItemObservation.projection_hash)
        .where(RawItemObservation.raw_item_id == raw_item_id)
        .order_by(RawItemObservation.created_at.desc(), RawItemObservation.id.desc())
        .limit(1)
    )
    kind = (
        RawItemObservationKind.FIRST_SEEN
        if inserted
        else RawItemObservationKind.DUPLICATE_SAME_PROJECTION
        if previous_hash == projection_hash
        else RawItemObservationKind.REVISION_CANDIDATE
    )
    values = {
        "collection_run_id": run_id,
        "raw_item_id": raw_item_id,
        "target_id": target_id,
        "source_id": source_id,
        "source_account_id": source_account_id,
        "provider": provider,
        "operation_key": operation_key,
        "config_revision": config_revision,
        "provider_contract_version": provider_contract_version,
        "observed_at": observed_at,
        "projection_hash": projection_hash,
        "observation_kind": kind,
        "observation_key": observation_key,
    }
    identity = await session.scalar(
        insert(RawItemObservation)
        .values(**values)
        .on_conflict_do_nothing(
            index_elements=[
                RawItemObservation.collection_run_id,
                RawItemObservation.raw_item_id,
                RawItemObservation.observation_key,
            ]
        )
        .returning(RawItemObservation.id)
    )
    if identity is not None:
        return identity
    existing = await session.scalar(
        select(RawItemObservation).where(
            RawItemObservation.collection_run_id == run_id,
            RawItemObservation.raw_item_id == raw_item_id,
            RawItemObservation.observation_key == observation_key,
        )
    )
    if existing is None or existing.projection_hash != projection_hash:
        raise DownstreamPersistenceError("raw_item_observation_idempotency_failed")
    return existing.id


async def _projection(
    session: AsyncSession,
    *,
    observation_id: UUID,
    raw_item_id: UUID,
    provider: str,
    operation_key: str,
    payload: dict[str, Any],
    projection_hash: str,
    quality: str,
) -> int:
    quality_status = SafeProjectionQualityStatus(quality)
    identity = await session.scalar(
        insert(SafeFactProjection)
        .values(
            observation_id=observation_id,
            raw_item_id=raw_item_id,
            provider=provider,
            operation_key=operation_key,
            projection_schema_version=1,
            factual_payload=payload,
            projection_hash=projection_hash,
            quality_status=quality_status,
            processing_status=SafeProjectionProcessingStatus.PENDING,
            attempt_count=0,
        )
        .on_conflict_do_nothing(
            index_elements=[
                SafeFactProjection.observation_id,
                SafeFactProjection.projection_schema_version,
            ]
        )
        .returning(SafeFactProjection.id)
    )
    if identity is not None:
        return 1
    existing = await session.scalar(
        select(SafeFactProjection).where(
            SafeFactProjection.observation_id == observation_id,
            SafeFactProjection.projection_schema_version == 1,
        )
    )
    if existing is None or existing.projection_hash != projection_hash:
        raise DownstreamPersistenceError("safe_fact_projection_idempotency_failed")
    return 0


async def _raw_item(
    session: AsyncSession,
    run_id: UUID,
    source_id: UUID,
    source_account_id: UUID | None,
    envelope: Any,
) -> tuple[UUID, bool]:
    values = {
        "source_id": source_id,
        "source_account_id": source_account_id,
        "collection_run_id": run_id,
        "external_id": envelope.external_id,
        "fetched_at": envelope.fetched_at,
        "http_status": envelope.http_status,
        "content_type": envelope.content_type,
        "payload_location": envelope.payload_location,
        "payload_hash": envelope.payload_hash,
        "retention_class": envelope.retention_class,
        "parse_status": ParseStatus.PENDING,
    }
    statement = insert(RawItem).values(**values)
    if envelope.external_id is not None:
        statement = statement.on_conflict_do_nothing(
            index_elements=[RawItem.source_id, RawItem.external_id],
            index_where=RawItem.external_id.is_not(None),
        )
        identity = RawItem.external_id == envelope.external_id
    else:
        if envelope.payload_hash is None:
            raise DownstreamPersistenceError("raw_item_identity_missing")
        statement = statement.on_conflict_do_nothing(
            index_elements=[RawItem.source_id, RawItem.payload_hash],
            index_where=RawItem.external_id.is_(None) & RawItem.payload_hash.is_not(None),
        )
        identity = RawItem.external_id.is_(None) & (RawItem.payload_hash == envelope.payload_hash)
    raw_id = await session.scalar(statement.returning(RawItem.id))
    if raw_id is not None:
        return raw_id, True
    existing = await session.scalar(
        select(RawItem.id).where(RawItem.source_id == source_id, identity).limit(1)
    )
    if existing is None:
        raise DownstreamPersistenceError("raw_item_idempotency_failed")
    return existing, False
