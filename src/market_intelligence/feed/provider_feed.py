"""Metadata-only visible ContentItem persistence for official provider evidence."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from market_intelligence.db.models import (
    BodyAvailability,
    ContentItem,
    ContentKind,
    DeletedStatus,
    RawItem,
)
from market_intelligence.evidence.end_to_end import InMemoryProviderProjectionSidecar
from market_intelligence.evidence.projection_store import SqlAlchemyRawItemProjectionReader


class ProviderFeedService:
    """Persist SEC filing labels only; numeric providers remain evidence-only."""

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory

    async def persist_sec_run(
        self,
        collection_run_id: uuid.UUID,
        sidecar: InMemoryProviderProjectionSidecar,
    ) -> int:
        count = 0
        async with self._factory() as session:
            raw_ids = tuple(
                await session.scalars(
                    select(RawItem.id).where(RawItem.collection_run_id == collection_run_id)
                )
            )
            reader = SqlAlchemyRawItemProjectionReader(session)
            for raw_id in raw_ids:
                raw = await reader.get(raw_id)
                display = None if raw is None else sidecar.display_metadata(raw)
                if raw is None or display is None:
                    continue
                title = display.get("display_title")
                item_id = display.get("provider_item_id")
                published_at = display.get("published_at")
                if not all(
                    isinstance(value, str) and value for value in (title, item_id, published_at)
                ):
                    continue
                assert isinstance(title, str)
                assert isinstance(item_id, str)
                assert isinstance(published_at, str)
                if await session.scalar(
                    select(ContentItem.id).where(ContentItem.raw_item_id == raw_id)
                ):
                    count += 1
                    continue
                session.add(
                    ContentItem(
                        raw_item_id=raw_id,
                        source_id=raw.source_id,
                        source_account_id=raw.source_account_id,
                        content_kind=ContentKind.OFFICIAL_RELEASE,
                        external_id=item_id,
                        title=title,
                        source_summary=None,
                        body=None,
                        body_availability=BodyAvailability.UNAVAILABLE,
                        author=None,
                        language=None,
                        original_url=None,
                        canonical_url=None,
                        source_published_at=datetime.fromisoformat(published_at),
                        source_updated_at=None,
                        first_seen_at=raw.fetched_at,
                        content_hash=None,
                        reply_to_external_id=None,
                        quote_external_id=None,
                        repost_external_id=None,
                        deleted_status=DeletedStatus.UNKNOWN,
                        metadata_={"provider": "sec_edgar", "retention": "metadata_only"},
                    )
                )
                count += 1
            await session.commit()
        return count
