"""Provider-neutral, transaction-bound persistence after a target fetch."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from market_intelligence.db.models import (
    AuditLog,
    BodyAvailability,
    ContentItem,
    ContentKind,
    DeletedStatus,
    ParseStatus,
    RawItem,
)
from market_intelligence.event_intelligence.service import EventCandidateService
from market_intelligence.evidence.orchestration import (
    EvidencePipelineRequest,
    EvidencePipelineService,
    EvidencePipelineStatus,
)
from market_intelligence.evidence.write_path import EvidenceWriteService
from market_intelligence.notifications.intent import create_pending_intent
from market_intelligence.providers.contracts import ProviderFetchResult


class DownstreamPersistenceError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class DownstreamCounts:
    fetched: int
    new: int
    duplicates: int
    content: int
    evidence: int
    notifications: int


async def persist_fetch_result(
    session: AsyncSession,
    *,
    run_id: UUID,
    source_id: UUID,
    source_account_id: UUID | None,
    provider: str,
    result: ProviderFetchResult,
) -> DownstreamCounts:
    """Persist one sanitized fetch atomically; never consumes provider raw response."""

    new_count = duplicate_count = content_count = evidence_count = notification_count = 0
    for envelope, metadata, display in zip(
        result.raw_items,
        result.sanitized_metadata,
        result.display_projections or tuple({} for _ in result.raw_items),
        strict=True,
    ):
        raw_id, inserted = await _raw_item(session, run_id, source_id, source_account_id, envelope)
        if inserted:
            new_count += 1
        else:
            duplicate_count += 1
        content_id = await _content_item(
            session,
            raw_id=raw_id,
            source_id=source_id,
            source_account_id=source_account_id,
            provider=provider,
            display=display,
            fetched_at=envelope.fetched_at,
        )
        content_count += int(content_id is not None)
        projection = dict(metadata)
        projection["payload_hash"] = envelope.payload_hash
        projection["payload_reference"] = envelope.payload_location
        outcome = await EvidencePipelineService(EvidenceWriteService(session)).process(
            EvidencePipelineRequest(
                raw_item_id=raw_id,
                source_id=source_id,
                source_account_id=source_account_id,
                provider=provider,
                sanitized_projection=projection,
                observed_at=envelope.fetched_at,
                correlation_id=f"collection-run:{run_id}",
                content_item_id=content_id,
            )
        )
        if outcome.status not in {
            EvidencePipelineStatus.WRITTEN,
            EvidencePipelineStatus.DUPLICATE,
        }:
            raise DownstreamPersistenceError("evidence_pipeline_failed")
        evidence_count += int(outcome.evidence_item_id is not None)
        if outcome.evidence_item_id is not None:
            await EventCandidateService().process(session, outcome.evidence_item_id)
        if content_id is not None:
            try:
                async with session.begin_nested():
                    notification_id = await create_pending_intent(session, content_id)
                notification_count += int(notification_id is not None)
            except Exception:
                session.add(
                    AuditLog(
                        actor_type="system",
                        actor_id=None,
                        action="notification_intent_recovery",
                        target_type="content_item",
                        target_id=content_id,
                        before=None,
                        after={
                            "policy_id": "spec-0038-multi-provider-telegram",
                            "policy_version": "1",
                            "status": "pending",
                            "safe_error": "notification_intent_pending_recovery",
                        },
                    )
                )
    return DownstreamCounts(
        len(result.raw_items),
        new_count,
        duplicate_count,
        content_count,
        evidence_count,
        notification_count,
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


async def _content_item(
    session: AsyncSession,
    *,
    raw_id: UUID,
    source_id: UUID,
    source_account_id: UUID | None,
    provider: str,
    display: Any,
    fetched_at: datetime,
) -> UUID | None:
    if not isinstance(display, dict) and not hasattr(display, "get"):
        return None
    item_id, published_at = display.get("provider_item_id"), display.get("published_at")
    title, url = display.get("display_title"), display.get("display_url")
    if not all(isinstance(value, str) and value for value in (item_id, published_at)):
        return None
    if title is not None and not isinstance(title, str):
        return None
    existing = await session.scalar(
        select(ContentItem.id).where(
            (ContentItem.raw_item_id == raw_id)
            | ((ContentItem.source_id == source_id) & (ContentItem.external_id == item_id))
        )
    )
    if existing is not None:
        return existing
    content_id = await session.scalar(
        insert(ContentItem)
        .values(
            raw_item_id=raw_id,
            source_id=source_id,
            source_account_id=source_account_id,
            content_kind=(
                ContentKind.OFFICIAL_RELEASE
                if provider in {"eia", "sec_edgar"}
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
            canonical_url=url if isinstance(url, str) else None,
            source_published_at=datetime.fromisoformat(published_at),
            source_updated_at=None,
            first_seen_at=fetched_at,
            content_hash=None,
            reply_to_external_id=None,
            quote_external_id=None,
            repost_external_id=None,
            deleted_status=DeletedStatus.UNKNOWN,
            metadata_={"provider": provider, "retention": "metadata_only"},
        )
        .on_conflict_do_nothing()
        .returning(ContentItem.id)
    )
    if content_id is not None:
        return content_id
    return cast(
        UUID | None,
        await session.scalar(
            select(ContentItem.id).where(
                (ContentItem.raw_item_id == raw_id)
                | ((ContentItem.source_id == source_id) & (ContentItem.external_id == item_id))
            )
        ),
    )
