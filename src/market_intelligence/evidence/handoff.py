"""Bounded R8-A SafeFactProjection to canonical Evidence durable handoff."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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
    SafeProjectionProcessingStatus,
)
from market_intelligence.evidence.provider_mappings import legacy_provider_item_identity
from market_intelligence.safe_projection.contracts import (
    ProjectionContractError,
    canonical_projection_hash,
    normalize_and_classify_factual_payload,
)


@dataclass(frozen=True, slots=True)
class EvidenceHandoffReport:
    claimed: int
    linked: int
    blocked: int
    retried: int
    recovered: int


class HandoffConflict(ValueError):
    pass


_ITEM_TYPE = {
    "marketaux": ("marketaux_news", "news", "news"),
    "finnhub": ("finnhub_quote", "market_data", "market_data"),
    "eia": ("eia_energy_timeseries", "energy_official", "official_energy"),
    "sec_edgar": ("sec_filing", "disclosure", "disclosure"),
}

_ACCESS_POLICY = {
    "marketaux": "link_only",
    "finnhub": "licensed",
    "eia": "public_summary",
    "sec_edgar": "link_only",
}


class EvidenceProjectionHandoffWorker:
    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        *,
        max_attempts: int = 3,
        stale_after: timedelta = timedelta(minutes=10),
        retry_delay: timedelta = timedelta(minutes=1),
    ) -> None:
        self._factory = factory
        self._max_attempts = max_attempts
        self._stale_after = stale_after
        self._retry_delay = retry_delay

    async def process_batch(self, *, limit: int = 100) -> EvidenceHandoffReport:
        if not 1 <= limit <= 500:
            raise ValueError("evidence_handoff_limit_invalid")
        now = datetime.now(UTC)
        recovered = await self._recover_stale(now, limit)
        await self._discover_ready(limit)
        claimed = await self._claim(limit, now)
        linked = blocked = retried = 0
        for identity in claimed:
            try:
                result = await self._link_one(identity, now)
            except Exception:
                result = await self._retry(identity, "evidence_handoff_unexpected", now)
            linked += result == "linked"
            blocked += result == "blocked"
            retried += result == "retry"
        return EvidenceHandoffReport(len(claimed), linked, blocked, retried, recovered)

    async def _discover_ready(self, limit: int) -> None:
        async with self._factory.begin() as session:
            await session.execute(
                text("""
                INSERT INTO evidence_projection_links(safe_fact_projection_id,status)
                SELECT p.id,'pending'::evidence_projection_link_status
                FROM safe_fact_projections p
                WHERE p.processing_status='ready'
                  AND NOT EXISTS (
                    SELECT 1 FROM evidence_projection_links l
                    WHERE l.safe_fact_projection_id=p.id
                  )
                ORDER BY p.created_at,p.id
                LIMIT :limit
                ON CONFLICT (safe_fact_projection_id) DO NOTHING
                """),
                {"limit": limit},
            )

    async def _recover_stale(self, now: datetime, limit: int) -> int:
        async with self._factory.begin() as session:
            rows = tuple(
                await session.scalars(
                    select(EvidenceProjectionLink)
                    .where(
                        EvidenceProjectionLink.status == EvidenceProjectionLinkStatus.PROCESSING,
                        EvidenceProjectionLink.updated_at < now - self._stale_after,
                    )
                    .order_by(EvidenceProjectionLink.updated_at, EvidenceProjectionLink.id)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            )
            for row in rows:
                if row.attempt_count >= self._max_attempts:
                    row.status = EvidenceProjectionLinkStatus.BLOCKED
                    row.safe_error_code = "evidence_handoff_retry_exhausted"
                    row.next_retry_at = None
                else:
                    row.status = EvidenceProjectionLinkStatus.RETRY
                    row.safe_error_code = "evidence_handoff_stale"
                    row.next_retry_at = now
                row.updated_at = now
            return len(rows)

    async def _claim(self, limit: int, now: datetime) -> tuple[UUID, ...]:
        async with self._factory.begin() as session:
            rows = tuple(
                await session.scalars(
                    select(EvidenceProjectionLink)
                    .join(SafeFactProjection)
                    .where(
                        SafeFactProjection.processing_status
                        == SafeProjectionProcessingStatus.READY,
                        EvidenceProjectionLink.status.in_(
                            (
                                EvidenceProjectionLinkStatus.PENDING,
                                EvidenceProjectionLinkStatus.RETRY,
                            )
                        ),
                        or_(
                            EvidenceProjectionLink.next_retry_at.is_(None),
                            EvidenceProjectionLink.next_retry_at <= now,
                        ),
                    )
                    .order_by(EvidenceProjectionLink.created_at, EvidenceProjectionLink.id)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            )
            for row in rows:
                row.status = EvidenceProjectionLinkStatus.PROCESSING
                row.attempt_count += 1
                row.safe_error_code = None
                row.next_retry_at = None
                row.updated_at = now
            return tuple(row.id for row in rows)

    async def _link_one(self, identity: UUID, now: datetime) -> str:
        try:
            async with self._factory.begin() as session:
                link = await session.get(EvidenceProjectionLink, identity, with_for_update=True)
                if link is None or link.status is not EvidenceProjectionLinkStatus.PROCESSING:
                    return "blocked"
                projection = await session.get(SafeFactProjection, link.safe_fact_projection_id)
                if (
                    projection is None
                    or projection.processing_status is not SafeProjectionProcessingStatus.READY
                ):
                    raise HandoffConflict("evidence_projection_not_ready")
                try:
                    normalized, _quality = normalize_and_classify_factual_payload(
                        projection.provider,
                        projection.operation_key,
                        projection.projection_schema_version,
                        projection.factual_payload,
                    )
                    if normalized != projection.factual_payload:
                        raise ProjectionContractError("projection_not_canonical")
                    if canonical_projection_hash(normalized) != projection.projection_hash:
                        raise ProjectionContractError("projection_hash_mismatch")
                except ProjectionContractError as exc:
                    raise HandoffConflict("evidence_projection_contract_invalid") from exc
                raw = await session.get(RawItem, projection.raw_item_id)
                observation = await session.get(RawItemObservation, projection.observation_id)
                if raw is None or observation is None:
                    raise HandoffConflict("evidence_projection_provenance_missing")
                content = await _content(session, projection, raw)
                evidence = await _evidence(session, projection, observation, raw, content)
                link.evidence_item_id = evidence.id
                link.content_item_id = None if content is None else content.id
                link.status = EvidenceProjectionLinkStatus.LINKED
                link.linked_at = now
                link.safe_error_code = None
                link.next_retry_at = None
                link.updated_at = now
            return "linked"
        except HandoffConflict as exc:
            await self._terminal(identity, str(exc), now)
            return "blocked"
        except IntegrityError:
            return await self._retry(identity, "evidence_handoff_integrity_conflict", now)

    async def _terminal(self, identity: UUID, code: str, now: datetime) -> None:
        async with self._factory.begin() as session:
            row = await session.get(EvidenceProjectionLink, identity, with_for_update=True)
            if row is not None and row.status is EvidenceProjectionLinkStatus.PROCESSING:
                row.status = EvidenceProjectionLinkStatus.BLOCKED
                row.safe_error_code = code
                row.next_retry_at = None
                row.updated_at = now

    async def _retry(self, identity: UUID, code: str, now: datetime) -> str:
        async with self._factory.begin() as session:
            row = await session.get(EvidenceProjectionLink, identity, with_for_update=True)
            if row is None or row.status is not EvidenceProjectionLinkStatus.PROCESSING:
                return "blocked"
            if row.attempt_count >= self._max_attempts:
                row.status = EvidenceProjectionLinkStatus.BLOCKED
                row.safe_error_code = "evidence_handoff_retry_exhausted"
                row.next_retry_at = None
                result = "blocked"
            else:
                row.status = EvidenceProjectionLinkStatus.RETRY
                row.safe_error_code = code
                row.next_retry_at = now + self._retry_delay
                result = "retry"
            row.updated_at = now
            return result


async def _content(
    session: AsyncSession, projection: SafeFactProjection, raw: RawItem
) -> ContentItem | None:
    payload = projection.factual_payload
    if projection.provider == "marketaux":
        if not all(
            isinstance(payload.get(k), str) and payload[k]
            for k in ("title", "canonical_url", "source_identity")
        ):
            return None
        kind = ContentKind.ARTICLE
        title = payload["title"]
        url = payload["canonical_url"]
    elif projection.provider == "sec_edgar":
        kind = ContentKind.OFFICIAL_RELEASE
        title = f"SEC {payload['form']} filing"
        url = payload["official_url"]
    else:
        return None
    existing = await session.scalar(select(ContentItem).where(ContentItem.raw_item_id == raw.id))
    if existing is not None:
        if (
            existing.source_id != raw.source_id
            or existing.source_account_id != raw.source_account_id
            or existing.content_kind is not kind
            or existing.external_id != payload["provider_item_id"]
        ):
            raise HandoffConflict("evidence_content_identity_conflict")
        return existing
    item = ContentItem(
        raw_item_id=raw.id,
        source_id=raw.source_id,
        source_account_id=raw.source_account_id,
        content_kind=kind,
        external_id=payload["provider_item_id"],
        title=title,
        source_summary=None,
        body=None,
        body_availability=BodyAvailability.UNAVAILABLE,
        author=None,
        language=payload.get("language") if projection.provider == "marketaux" else None,
        original_url=url,
        canonical_url=url,
        source_published_at=datetime.fromisoformat(payload["published_at"]),
        source_updated_at=None,
        first_seen_at=raw.fetched_at,
        content_hash=None,
        reply_to_external_id=None,
        quote_external_id=None,
        repost_external_id=None,
        deleted_status=DeletedStatus.UNKNOWN,
        metadata_={
            "provider": projection.provider,
            "retention": "metadata_only" if projection.provider == "marketaux" else "link_only",
        },
    )
    session.add(item)
    await session.flush()
    return item


async def _evidence(
    session: AsyncSession,
    projection: SafeFactProjection,
    observation: RawItemObservation,
    raw: RawItem,
    content: ContentItem | None,
) -> EvidenceItem:
    item_type, evidence_kind, source_type = _ITEM_TYPE.get(projection.provider, (None, None, None))
    if item_type is None:
        raise HandoffConflict("evidence_provider_unsupported")
    provider_item_id = str(projection.factual_payload["provider_item_id"])
    legacy_item_id = legacy_provider_item_identity(projection.provider, projection.factual_payload)
    existing = tuple(
        await session.scalars(
            select(EvidenceItem).where(
                EvidenceItem.raw_item_id == raw.id,
                EvidenceItem.provider == projection.provider,
            )
        )
    )
    if len(existing) > 1:
        raise HandoffConflict("evidence_canonical_not_unique")
    if existing:
        item = existing[0]
        if (
            item.provider_item_type != item_type
            or item.source_id != raw.source_id
            or item.source_account_id != raw.source_account_id
            or item.provider_item_id not in {provider_item_id, legacy_item_id}
            or (content is not None and item.content_item_id not in (None, content.id))
            or (content is None and item.content_item_id is not None)
        ):
            raise HandoffConflict("evidence_canonical_identity_conflict")
        return item
    conflict = await session.scalar(
        select(EvidenceItem.id).where(
            EvidenceItem.provider == projection.provider,
            or_(
                EvidenceItem.provider_item_id == provider_item_id,
                EvidenceItem.provider_item_hash == projection.projection_hash,
            ),
        )
    )
    if conflict is not None:
        raise HandoffConflict("evidence_canonical_identity_conflict")
    is_market = projection.provider == "finnhub"
    is_official = projection.provider in {"eia", "sec_edgar"}
    is_disclosure = projection.provider == "sec_edgar"
    is_news = projection.provider == "marketaux"
    payload: dict[str, Any] = projection.factual_payload
    item = EvidenceItem(
        evidence_version=1,
        provider=projection.provider,
        provider_item_type=item_type,
        evidence_kind=evidence_kind,
        source_type=source_type,
        source_id=raw.source_id,
        source_account_id=raw.source_account_id,
        raw_item_id=raw.id,
        content_item_id=None if content is None else content.id,
        provider_item_id=provider_item_id,
        provider_item_hash=projection.projection_hash,
        event_time=datetime.fromisoformat(payload["published_at"]),
        observed_at=observation.observed_at,
        access_level=_ACCESS_POLICY[projection.provider],
        processing_status="validated",
        official_source_flag=is_official,
        market_data_flag=is_market,
        disclosure_flag=is_disclosure,
        news_signal_flag=is_news,
        content_presence={
            "has_title": bool(payload.get("title")),
            "has_body": False,
            "has_url": bool(payload.get("canonical_url") or payload.get("official_url")),
            "has_snippet": False,
            "has_description": False,
        },
        numeric_presence={
            "has_numeric_value": projection.provider in {"finnhub", "eia"},
            "numeric_field_count": 7 if is_market else 1 if projection.provider == "eia" else 0,
            "nullable_allowed": projection.provider == "eia",
        },
        entity_refs=[],
        asset_refs=[],
        topic_refs=[],
        raw_payload_reference=f"internal://safe-fact-projection/{projection.id}",
        errors=[],
    )
    session.add(item)
    await session.flush()
    return item
