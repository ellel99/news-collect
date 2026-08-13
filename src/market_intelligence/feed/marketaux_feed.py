"""Minimal Marketaux ContentItem projection and read-only visible feed."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from market_intelligence.db.models import (
    BodyAvailability,
    ContentItem,
    ContentKind,
    DeletedStatus,
    EvidenceItem,
    RawItem,
    Source,
)
from market_intelligence.evidence.end_to_end import InMemoryProviderProjectionSidecar
from market_intelligence.evidence.projection_store import SqlAlchemyRawItemProjectionReader


@dataclass(frozen=True, slots=True)
class VisibleFeedItem:
    content_item_id: uuid.UUID
    title: str
    source: str
    provider: str
    published_at: datetime
    canonical_url: str
    provider_item_id: str
    collected_at: datetime
    raw_item_id: uuid.UUID
    evidence_item_id: uuid.UUID | None


class MarketauxFeedService:
    """Persist allowlisted display fields and query the recent Marketaux feed."""

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory

    async def persist_run(
        self,
        collection_run_id: uuid.UUID,
        sidecar: InMemoryProviderProjectionSidecar,
    ) -> int:
        available = 0
        async with self._factory() as session:
            raw_ids = tuple(
                await session.scalars(
                    select(RawItem.id)
                    .where(RawItem.collection_run_id == collection_run_id)
                    .order_by(RawItem.id)
                )
            )
            reader = SqlAlchemyRawItemProjectionReader(session)
            for raw_id in raw_ids:
                raw = await reader.get(raw_id)
                metadata = None if raw is None else sidecar.display_metadata(raw)
                if raw is None or metadata is None:
                    continue
                display = _validate_display(metadata)
                if display is None:
                    continue
                existing = await session.scalar(
                    select(ContentItem.id).where(
                        or_(
                            ContentItem.raw_item_id == raw.raw_item_id,
                            (
                                (ContentItem.source_id == raw.source_id)
                                & (ContentItem.external_id == display.provider_item_id)
                            ),
                        )
                    )
                )
                if existing is not None:
                    available += 1
                    continue
                session.add(
                    ContentItem(
                        raw_item_id=raw.raw_item_id,
                        source_id=raw.source_id,
                        source_account_id=raw.source_account_id,
                        content_kind=ContentKind.ARTICLE,
                        external_id=display.provider_item_id,
                        title=display.title,
                        source_summary=display.summary,
                        body=None,
                        body_availability=BodyAvailability.UNAVAILABLE,
                        author=None,
                        language=None,
                        original_url=display.canonical_url,
                        canonical_url=display.canonical_url,
                        source_published_at=display.published_at,
                        source_updated_at=None,
                        first_seen_at=raw.fetched_at,
                        content_hash=None,
                        reply_to_external_id=None,
                        quote_external_id=None,
                        repost_external_id=None,
                        deleted_status=DeletedStatus.UNKNOWN,
                        metadata_={
                            "provider": "marketaux",
                            "retention": "metadata_only",
                            "public_url": display.canonical_url,
                        },
                    )
                )
                available += 1
            await session.commit()
        return available

    async def recent(self, limit: int = 10) -> tuple[VisibleFeedItem, ...]:
        if limit < 1 or limit > 50:
            raise ValueError("feed_limit_invalid")
        async with self._factory() as session:
            rows = (
                await session.execute(
                    select(ContentItem, Source.name, EvidenceItem.id)
                    .join(Source, Source.id == ContentItem.source_id)
                    .outerjoin(EvidenceItem, EvidenceItem.raw_item_id == ContentItem.raw_item_id)
                    .where(
                        Source.access_method == "marketaux",
                        ContentItem.title.is_not(None),
                        ContentItem.canonical_url.is_not(None),
                        ContentItem.source_published_at.is_not(None),
                    )
                    .order_by(ContentItem.source_published_at.desc(), ContentItem.id.desc())
                    .limit(limit)
                )
            ).all()
        return tuple(
            VisibleFeedItem(
                content_item_id=content.id,
                title=content.title or "",
                source=source_name,
                provider="marketaux",
                published_at=content.source_published_at,
                canonical_url=content.canonical_url or "",
                provider_item_id=content.external_id or "",
                collected_at=content.first_seen_at,
                raw_item_id=content.raw_item_id,
                evidence_item_id=evidence_id,
            )
            for content, source_name, evidence_id in rows
        )

    async def for_run(
        self, collection_run_id: uuid.UUID, limit: int
    ) -> tuple[VisibleFeedItem, ...]:
        """Return only visible items associated with one collection run."""

        if limit < 1 or limit > 5:
            raise ValueError("feed_limit_invalid")
        async with self._factory() as session:
            rows = (
                await session.execute(
                    select(ContentItem, Source.name, EvidenceItem.id)
                    .join(RawItem, RawItem.id == ContentItem.raw_item_id)
                    .join(Source, Source.id == ContentItem.source_id)
                    .outerjoin(EvidenceItem, EvidenceItem.raw_item_id == ContentItem.raw_item_id)
                    .where(
                        RawItem.collection_run_id == collection_run_id,
                        Source.access_method == "marketaux",
                        ContentItem.title.is_not(None),
                        ContentItem.canonical_url.is_not(None),
                        ContentItem.source_published_at.is_not(None),
                    )
                    .order_by(ContentItem.source_published_at.desc(), ContentItem.id.desc())
                    .limit(limit)
                )
            ).all()
        return tuple(
            VisibleFeedItem(
                content_item_id=content.id,
                title=content.title or "",
                source=source_name,
                provider="marketaux",
                published_at=content.source_published_at,
                canonical_url=content.canonical_url or "",
                provider_item_id=content.external_id or "",
                collected_at=content.first_seen_at,
                raw_item_id=content.raw_item_id,
                evidence_item_id=evidence_id,
            )
            for content, source_name, evidence_id in rows
        )

    async def by_content_ids(
        self, content_item_ids: tuple[uuid.UUID, ...]
    ) -> tuple[VisibleFeedItem, ...]:
        """Return visible Marketaux items for already-claimed notifications."""

        if not content_item_ids:
            return ()
        async with self._factory() as session:
            rows = (
                await session.execute(
                    select(ContentItem, Source.name, EvidenceItem.id)
                    .join(Source, Source.id == ContentItem.source_id)
                    .outerjoin(EvidenceItem, EvidenceItem.raw_item_id == ContentItem.raw_item_id)
                    .where(
                        ContentItem.id.in_(content_item_ids),
                        Source.access_method == "marketaux",
                        ContentItem.title.is_not(None),
                        ContentItem.canonical_url.is_not(None),
                        ContentItem.source_published_at.is_not(None),
                    )
                )
            ).all()
        by_id = {
            content.id: VisibleFeedItem(
                content_item_id=content.id,
                title=content.title or "",
                source=source_name,
                provider="marketaux",
                published_at=content.source_published_at,
                canonical_url=content.canonical_url or "",
                provider_item_id=content.external_id or "",
                collected_at=content.first_seen_at,
                raw_item_id=content.raw_item_id,
                evidence_item_id=evidence_id,
            )
            for content, source_name, evidence_id in rows
        }
        return tuple(by_id[item_id] for item_id in content_item_ids if item_id in by_id)


@dataclass(frozen=True, slots=True)
class _DisplayProjection:
    title: str
    canonical_url: str
    provider_item_id: str
    published_at: datetime
    summary: str | None


def _validate_display(metadata: Mapping[str, object]) -> _DisplayProjection | None:
    title = metadata.get("display_title")
    url = metadata.get("display_url")
    item_id = metadata.get("provider_item_id")
    published = metadata.get("published_at")
    summary = metadata.get("display_summary")
    if not all(isinstance(value, str) and value for value in (title, url, item_id, published)):
        return None
    assert isinstance(title, str) and isinstance(url, str)
    assert isinstance(item_id, str) and isinstance(published, str)
    parsed_url = urlsplit(url)
    if parsed_url.scheme not in ("http", "https") or not parsed_url.netloc:
        return None
    try:
        published_at = datetime.fromisoformat(published.replace("Z", "+00:00"))
    except ValueError:
        return None
    if published_at.tzinfo is None:
        return None
    if summary is not None and (not isinstance(summary, str) or not summary or len(summary) > 1000):
        return None
    return _DisplayProjection(title, url, item_id, published_at, summary)
