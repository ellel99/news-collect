"""R1 provider-neutral RawItem persistence after a target fetch."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from market_intelligence.db.models import ParseStatus, RawItem
from market_intelligence.providers.contracts import ProviderFetchResult


class DownstreamPersistenceError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class DownstreamCounts:
    fetched: int
    new: int
    duplicates: int


async def persist_fetch_result(
    session: AsyncSession,
    *,
    run_id: UUID,
    source_id: UUID,
    source_account_id: UUID | None,
    provider: str,
    result: ProviderFetchResult,
) -> DownstreamCounts:
    """Persist canonical RawItems only; R2/R8 own durable projection and evidence."""

    del provider
    new_count = duplicate_count = 0
    for envelope in result.raw_items:
        _, inserted = await _raw_item(session, run_id, source_id, source_account_id, envelope)
        if inserted:
            new_count += 1
        else:
            duplicate_count += 1
    return DownstreamCounts(
        len(result.raw_items),
        new_count,
        duplicate_count,
    )


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
