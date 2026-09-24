"""Bounded R8-A SafeFactProjection to canonical Evidence durable handoff."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from market_intelligence.db.models import (
    BodyAvailability,
    CollectionRun,
    CollectionTarget,
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
    Source,
    SourceAccount,
)
from market_intelligence.evidence.provider_mappings import legacy_provider_item_identity
from market_intelligence.providers.operation_policy import factual_operation_policy
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
                JOIN raw_item_observations o ON o.id=p.observation_id
                WHERE p.processing_status='ready'
                  AND NOT EXISTS (
                    SELECT 1 FROM evidence_projection_links l
                    WHERE l.safe_fact_projection_id=p.id
                  )
                ORDER BY o.observed_at,p.id
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
                    .join(
                        RawItemObservation,
                        RawItemObservation.id == SafeFactProjection.observation_id,
                    )
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
                    .order_by(
                        RawItemObservation.observed_at,
                        SafeFactProjection.id,
                        EvidenceProjectionLink.id,
                    )
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
                # Mutation triggers deliberately do not acquire advisory locks: a
                # BEFORE trigger runs after PostgreSQL has acquired the row lock,
                # so mixing row->advisory and advisory->row orders can deadlock.
                # Handoff instead uses a short, local row-lock timeout and a fixed
                # row order. A concurrent writer either finishes first or this
                # item becomes a bounded, value-free retry.
                await session.execute(text("SET LOCAL lock_timeout = '2s'"))
                link = await session.get(EvidenceProjectionLink, identity)
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
                raw_id = raw.id
                source_id = raw.source_id
                source_account_id = raw.source_account_id
                projection_id = projection.id
                observation_id = observation.id
                run_id = observation.collection_run_id
                target_id = observation.target_id
                # Fixed handoff row order. Ordinary mutations lock only their own
                # row and never wait on a later advisory lock, eliminating the
                # reverse-wait cycle that existed in 0010's original triggers.
                source = await session.get(
                    Source, source_id, with_for_update=True, populate_existing=True
                )
                raw = await session.get(
                    RawItem, raw_id, with_for_update=True, populate_existing=True
                )
                projection = await session.get(
                    SafeFactProjection, projection_id, with_for_update=True, populate_existing=True
                )
                observation = await session.get(
                    RawItemObservation,
                    observation_id,
                    with_for_update=True,
                    populate_existing=True,
                )
                run = await session.get(
                    CollectionRun,
                    run_id,
                    with_for_update=True,
                    populate_existing=True,
                )
                account = (
                    None
                    if source_account_id is None
                    else await session.get(
                        SourceAccount,
                        source_account_id,
                        with_for_update=True,
                        populate_existing=True,
                    )
                )
                target = (
                    None
                    if target_id is None
                    else await session.get(
                        CollectionTarget,
                        target_id,
                        with_for_update=True,
                        populate_existing=True,
                    )
                )
                link = await session.get(
                    EvidenceProjectionLink, identity, with_for_update=True, populate_existing=True
                )
                if (
                    raw is None
                    or projection is None
                    or observation is None
                    or link is None
                    or link.status is not EvidenceProjectionLinkStatus.PROCESSING
                ):
                    raise HandoffConflict("evidence_projection_concurrent_change")
                # All factual and provenance checks are intentionally repeated only
                # after the final lock set. Values inspected before locking are never
                # consumed to create downstream state.
                try:
                    normalized, locked_quality = normalize_and_classify_factual_payload(
                        projection.provider,
                        projection.operation_key,
                        projection.projection_schema_version,
                        projection.factual_payload,
                    )
                    if normalized != projection.factual_payload:
                        raise ProjectionContractError("projection_not_canonical")
                    if canonical_projection_hash(normalized) != projection.projection_hash:
                        raise ProjectionContractError("projection_hash_mismatch")
                    if locked_quality != projection.quality_status.value:
                        raise ProjectionContractError("projection_quality_mismatch")
                    policy = factual_operation_policy(
                        projection.provider,
                        projection.operation_key,
                        observation.provider_contract_version,
                    )
                except (ProjectionContractError, ValueError) as exc:
                    raise HandoffConflict("evidence_projection_contract_invalid") from exc
                if (
                    run is None
                    or source is None
                    or projection.raw_item_id != raw.id
                    or observation.raw_item_id != raw.id
                    or observation.provider != projection.provider
                    or observation.operation_key != projection.operation_key
                    or observation.projection_hash != projection.projection_hash
                    or observation.collection_run_id != run.id
                    or observation.source_id != raw.source_id
                    or observation.source_account_id != raw.source_account_id
                    or run.source_id != raw.source_id
                    or run.source_account_id != raw.source_account_id
                    or run.target_id != observation.target_id
                    or source.id != raw.source_id
                    or source.access_method != projection.provider
                    or source.retention_class != raw.retention_class
                    or (raw.source_account_id is not None and account is None)
                    or (account is not None and account.source_id != raw.source_id)
                    or (
                        target is not None
                        and (
                            target.source_id != raw.source_id
                            or target.source_account_id != raw.source_account_id
                            or target.operation_key != projection.operation_key
                        )
                    )
                    or (target is None and observation.target_id is not None)
                    or raw.retention_class not in policy.retention
                ):
                    raise HandoffConflict("evidence_projection_provenance_invalid")
                content = await _content(session, projection, raw)
                evidence = await _evidence(session, projection, observation, raw, content)
                prior_evidence_link = await session.scalar(
                    select(EvidenceProjectionLink.id).where(
                        EvidenceProjectionLink.evidence_item_id == evidence.id,
                        EvidenceProjectionLink.status == EvidenceProjectionLinkStatus.LINKED,
                    )
                )
                prior_content_link = (
                    None
                    if content is None
                    else await session.scalar(
                        select(EvidenceProjectionLink.id).where(
                            EvidenceProjectionLink.content_item_id == content.id,
                            EvidenceProjectionLink.status == EvidenceProjectionLinkStatus.LINKED,
                        )
                    )
                )
                link.evidence_item_id = evidence.id
                link.content_item_id = None if content is None else content.id
                link.canonical_evidence = prior_evidence_link is None
                link.canonical_content = content is not None and prior_content_link is None
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
        except DBAPIError as exc:
            if _is_retryable_database_concurrency(exc):
                return await self._retry(identity, "evidence_handoff_concurrent_write", now)
            return await self._retry(identity, "evidence_handoff_database_error", now)

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


def _is_retryable_database_concurrency(exc: DBAPIError) -> bool:
    """Classify PostgreSQL concurrency failures without exposing DB details."""
    original = exc.orig
    sqlstate = getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)
    return sqlstate in {"40P01", "40001", "55P03", "57014"}


async def _content(
    session: AsyncSession, projection: SafeFactProjection, raw: RawItem
) -> ContentItem | None:
    payload = projection.factual_payload
    policy = factual_operation_policy(projection.provider, projection.operation_key)
    if policy.content == "article":
        kind = ContentKind.ARTICLE
        existing: ContentItem | None = await session.scalar(
            select(ContentItem).where(ContentItem.raw_item_id == raw.id).with_for_update()
        )
        if existing is not None:
            await _validate_existing_content(session, existing, projection, raw, kind)
            return existing
        if not all(
            isinstance(payload.get(k), str) and payload[k]
            for k in ("title", "canonical_url", "source_identity")
        ):
            return None
        title = payload["title"]
        url = payload["canonical_url"]
    elif policy.content == "official_release":
        kind = ContentKind.OFFICIAL_RELEASE
        existing = await session.scalar(
            select(ContentItem).where(ContentItem.raw_item_id == raw.id).with_for_update()
        )
        if existing is not None:
            await _validate_existing_content(session, existing, projection, raw, kind)
            return existing
        title = f"SEC {payload['form']} filing"
        url = payload["official_url"]
    else:
        return None
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
            "operation_key": projection.operation_key,
            "retention": raw.retention_class,
        },
    )
    session.add(item)
    await session.flush()
    return item


async def _validate_existing_content(
    session: AsyncSession,
    content: ContentItem,
    projection: SafeFactProjection,
    raw: RawItem,
    kind: ContentKind,
) -> None:
    """Validate adopted canonical content against its originating projection."""
    canonical_projection = await session.scalar(
        select(SafeFactProjection)
        .join(EvidenceProjectionLink)
        .where(
            EvidenceProjectionLink.content_item_id == content.id,
            EvidenceProjectionLink.status == EvidenceProjectionLinkStatus.LINKED,
            EvidenceProjectionLink.canonical_content.is_(True),
        )
    )
    origin = canonical_projection or projection
    payload = origin.factual_payload
    expected_url = payload.get("canonical_url") or payload.get("official_url")
    expected_title = (
        payload.get("title")
        if origin.provider != "sec_edgar"
        else f"SEC {payload.get('form')} filing"
    )
    expected_language = payload.get("language") if origin.provider == "marketaux" else None
    expected_metadata = {
        "provider": origin.provider,
        "operation_key": origin.operation_key,
        "retention": raw.retention_class,
    }
    if (
        content.source_id != raw.source_id
        or content.source_account_id != raw.source_account_id
        or content.raw_item_id != raw.id
        or content.content_kind is not kind
        or content.external_id != payload.get("provider_item_id")
        or content.title != expected_title
        or content.original_url != expected_url
        or content.canonical_url != expected_url
        or content.source_published_at != datetime.fromisoformat(str(payload["published_at"]))
        or content.language != expected_language
        or content.body_availability is not BodyAvailability.UNAVAILABLE
        or content.body is not None
        or content.source_summary is not None
        or content.author is not None
        or content.content_hash is not None
        or content.source_updated_at is not None
        or content.reply_to_external_id is not None
        or content.quote_external_id is not None
        or content.repost_external_id is not None
        or content.deleted_status is not DeletedStatus.UNKNOWN
        or content.metadata_ != expected_metadata
    ):
        raise HandoffConflict("evidence_content_policy_invalid")


async def _evidence(
    session: AsyncSession,
    projection: SafeFactProjection,
    observation: RawItemObservation,
    raw: RawItem,
    content: ContentItem | None,
) -> EvidenceItem:
    policy = factual_operation_policy(projection.provider, projection.operation_key)
    item_type, evidence_kind, source_type = (
        policy.item_type,
        policy.evidence_kind,
        policy.source_type,
    )
    provider_item_id = str(projection.factual_payload["provider_item_id"])
    existing = tuple(
        await session.scalars(
            select(EvidenceItem)
            .where(
                EvidenceItem.raw_item_id == raw.id,
                EvidenceItem.provider == projection.provider,
            )
            .with_for_update()
        )
    )
    if len(existing) > 1:
        raise HandoffConflict("evidence_canonical_not_unique")
    if existing:
        item = existing[0]
        canonical_row = (
            await session.execute(
                select(SafeFactProjection, RawItemObservation)
                .join(
                    EvidenceProjectionLink,
                    EvidenceProjectionLink.safe_fact_projection_id == SafeFactProjection.id,
                )
                .join(
                    RawItemObservation,
                    RawItemObservation.id == SafeFactProjection.observation_id,
                )
                .where(
                    EvidenceProjectionLink.evidence_item_id == item.id,
                    EvidenceProjectionLink.status == EvidenceProjectionLinkStatus.LINKED,
                    EvidenceProjectionLink.canonical_evidence.is_(True),
                )
            )
        ).one_or_none()
        origin_projection, origin_observation = (
            canonical_row if canonical_row is not None else (projection, observation)
        )
        if (
            origin_projection.provider != projection.provider
            or origin_projection.operation_key != projection.operation_key
            or origin_projection.factual_payload.get("provider_item_id")
            != projection.factual_payload.get("provider_item_id")
        ):
            raise HandoffConflict("evidence_canonical_identity_conflict")
        origin_payload = origin_projection.factual_payload
        origin_plain_id = str(origin_payload["provider_item_id"])
        origin_legacy_id = (
            origin_plain_id
            if projection.operation_key in {"company_news", "electricity_rto_region_data"}
            else legacy_provider_item_identity(projection.provider, origin_payload)
        )
        legacy_identity = (
            item.provider_item_id == origin_legacy_id and origin_legacy_id != origin_plain_id
        )
        expected_access = (
            "link_only"
            if legacy_identity
            and (projection.provider, projection.operation_key)
            in {("finnhub", "quote"), ("eia", "electricity_retail_sales")}
            else policy.access
        )
        payload = origin_payload
        is_market = projection.operation_key == "quote"
        is_official = projection.provider in {"eia", "sec_edgar"}
        is_disclosure = projection.provider == "sec_edgar"
        is_news = policy.evidence_kind == "news"
        if (
            item.provider_item_type != item_type
            or item.source_id != raw.source_id
            or item.source_account_id != raw.source_account_id
            or item.provider_item_id not in {origin_plain_id, origin_legacy_id}
            or (content is not None and item.content_item_id not in (None, content.id))
            or (content is None and item.content_item_id is not None)
            or item.evidence_kind != evidence_kind
            or item.source_type != source_type
            or item.access_level != expected_access
            or item.event_time != datetime.fromisoformat(str(payload["published_at"]))
            or item.observed_at != origin_observation.observed_at
            or item.processing_status != "validated"
            or item.official_source_flag != is_official
            or item.market_data_flag != is_market
            or item.disclosure_flag != is_disclosure
            or item.news_signal_flag != is_news
            or item.content_presence
            != {
                "has_title": bool(payload.get("title")),
                "has_body": False,
                "has_url": bool(payload.get("canonical_url") or payload.get("official_url")),
                "has_snippet": False,
                "has_description": False,
            }
            or item.numeric_presence
            != {
                "has_numeric_value": is_market or projection.provider == "eia",
                "numeric_field_count": 7 if is_market else 1 if projection.provider == "eia" else 0,
                "nullable_allowed": projection.provider == "eia",
            }
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
    is_market = projection.operation_key == "quote"
    is_official = projection.provider in {"eia", "sec_edgar"}
    is_disclosure = projection.provider == "sec_edgar"
    is_news = policy.evidence_kind == "news"
    new_payload: dict[str, Any] = projection.factual_payload
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
        event_time=datetime.fromisoformat(new_payload["published_at"]),
        observed_at=observation.observed_at,
        access_level=policy.access,
        processing_status="validated",
        official_source_flag=is_official,
        market_data_flag=is_market,
        disclosure_flag=is_disclosure,
        news_signal_flag=is_news,
        content_presence={
            "has_title": bool(new_payload.get("title")),
            "has_body": False,
            "has_url": bool(new_payload.get("canonical_url") or new_payload.get("official_url")),
            "has_snippet": False,
            "has_description": False,
        },
        numeric_presence={
            "has_numeric_value": is_market or projection.provider == "eia",
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
