"""Metadata-only visible ContentItem persistence for official provider evidence."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from market_intelligence.db.models import (
    BodyAvailability,
    ContentItem,
    ContentKind,
    DeletedStatus,
    RawItem,
    Source,
)
from market_intelligence.evidence.end_to_end import InMemoryProviderProjectionSidecar
from market_intelligence.evidence.projection_store import SqlAlchemyRawItemProjectionReader


@dataclass(frozen=True, slots=True)
class ProviderDisplayItem:
    content_item_id: uuid.UUID
    provider: str
    title: str
    source: str
    published_at: datetime
    canonical_url: str | None


class ProviderFeedService:
    """Persist and read allowlisted, content-safe provider display projections."""

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory

    async def persist_run(
        self,
        collection_run_id: uuid.UUID,
        sidecar: InMemoryProviderProjectionSidecar,
        provider: str = "sec_edgar",
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
                    select(ContentItem.id).where(
                        or_(
                            ContentItem.raw_item_id == raw_id,
                            (ContentItem.source_id == raw.source_id)
                            & (ContentItem.external_id == item_id),
                        )
                    )
                ):
                    count += 1
                    continue
                session.add(
                    ContentItem(
                        raw_item_id=raw_id,
                        source_id=raw.source_id,
                        source_account_id=raw.source_account_id,
                        content_kind=(
                            ContentKind.OFFICIAL_RELEASE
                            if provider in {"sec_edgar", "eia"}
                            else ContentKind.FEED_ENTRY
                        ),
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
                        metadata_={"provider": provider, "retention": "metadata_only"},
                    )
                )
                count += 1
            await session.commit()
        return count

    async def for_run(
        self, collection_run_id: uuid.UUID, provider: str, limit: int
    ) -> tuple[ProviderDisplayItem, ...]:
        if not 1 <= limit <= 5:
            raise ValueError("feed_limit_invalid")
        async with self._factory() as session:
            rows = (
                await session.execute(
                    select(ContentItem, Source.name)
                    .join(RawItem, RawItem.id == ContentItem.raw_item_id)
                    .join(Source, Source.id == ContentItem.source_id)
                    .where(
                        RawItem.collection_run_id == collection_run_id,
                        Source.access_method == provider,
                        ContentItem.title.is_not(None),
                        ContentItem.source_published_at.is_not(None),
                    )
                    .order_by(ContentItem.source_published_at.desc(), ContentItem.id.desc())
                    .limit(limit)
                )
            ).all()
        return tuple(
            ProviderDisplayItem(
                content.id,
                provider,
                content.title or "",
                source_name,
                content.source_published_at,
                content.canonical_url,
            )
            for content, source_name in rows
        )

    async def by_content_ids(
        self, content_item_ids: tuple[uuid.UUID, ...]
    ) -> tuple[ProviderDisplayItem, ...]:
        if not content_item_ids:
            return ()
        async with self._factory() as session:
            rows = (
                await session.execute(
                    select(ContentItem, Source.name, Source.access_method)
                    .join(Source, Source.id == ContentItem.source_id)
                    .where(ContentItem.id.in_(content_item_ids))
                )
            ).all()
        by_id = {
            content.id: ProviderDisplayItem(
                content.id,
                provider,
                content.title or "",
                source_name,
                content.source_published_at,
                content.canonical_url,
            )
            for content, source_name, provider in rows
            if content.title is not None and content.source_published_at is not None
        }
        return tuple(by_id[item_id] for item_id in content_item_ids if item_id in by_id)
