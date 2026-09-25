"""Deterministic, bounded and read-only Rich Evidence Packet construction."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Literal, cast

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from market_intelligence.db.models import (
    CollectionRun,
    CollectionTarget,
    ContentItem,
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
from market_intelligence.evidence.provider_mappings import (
    LEGACY_OPAQUE_IDENTITY_OPERATIONS,
    legacy_provider_item_identity,
)
from market_intelligence.providers.operation_policy import factual_operation_policy
from market_intelligence.rich_evidence.contracts import (
    ContentReference,
    EiaRetailFacts,
    EiaRtoFacts,
    EvidenceRevision,
    FinnhubCompanyNewsFacts,
    FinnhubQuoteFacts,
    MarketauxNewsFacts,
    PacketPage,
    PacketTruncation,
    RichEvidenceError,
    RichEvidencePacket,
    SecFilingFacts,
    SourceProvenance,
    TypedFacts,
)
from market_intelligence.safe_projection.contracts import (
    ProjectionContractError,
    canonical_projection_hash,
    normalize_and_classify_factual_payload,
)

_EXPECTED_TYPE = {
    ("marketaux", "news_all"): "marketaux_news",
    ("finnhub", "quote"): "finnhub_quote",
    ("finnhub", "company_news"): "finnhub_company_news",
    ("eia", "electricity_retail_sales"): "eia_energy_timeseries",
    ("eia", "electricity_rto_region_data"): "eia_energy_timeseries",
    ("sec_edgar", "submissions_recent"): "sec_filing",
}

_PREFETCH_CHUNK_SIZE = 100
_QUERIES_PER_PREFETCH_CHUNK = 7


def packet_query_budget(scan_limit: int) -> int:
    """Return the fixed upper query gate for a bounded packet scan."""
    if not 1 <= scan_limit <= 500:
        raise ValueError("rich_evidence_scan_limit_invalid")
    # One statement establishes the read-only repeatable-read snapshot; each
    # subsequent bounded chunk uses one evidence scan plus six set-based loads.
    return 1 + math.ceil(scan_limit / _PREFETCH_CHUNK_SIZE) * _QUERIES_PER_PREFETCH_CHUNK


@dataclasses.dataclass(frozen=True)
class _PacketInputs:
    rows: Mapping[uuid.UUID, tuple[Any, ...]]
    raws: Mapping[uuid.UUID, RawItem]
    sources: Mapping[uuid.UUID, Source]
    accounts: Mapping[uuid.UUID, SourceAccount]
    first_runs: Mapping[uuid.UUID, CollectionRun]
    contents: Mapping[uuid.UUID, ContentItem]


class RichEvidencePacketBuilder:
    """Build packets without mutating the database or reading RawItem payload storage."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        max_revisions: int = 50,
        max_serialized_bytes: int = 262_144,
        max_batch_size: int = 100,
    ) -> None:
        if not 1 <= max_revisions <= 500:
            raise ValueError("rich_evidence_revision_budget_invalid")
        if not 1_024 <= max_serialized_bytes <= 2_000_000:
            raise ValueError("rich_evidence_size_budget_invalid")
        if not 1 <= max_batch_size <= 500:
            raise ValueError("rich_evidence_batch_budget_invalid")
        self._factory = session_factory
        self._max_revisions = max_revisions
        self._max_bytes = max_serialized_bytes
        self._max_batch = max_batch_size

    async def build_one(self, evidence_id: uuid.UUID) -> RichEvidencePacket:
        async with self._factory() as session, session.begin():
            await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            evidence = await session.get(EvidenceItem, evidence_id)
            if evidence is None:
                raise RichEvidenceError("rich_evidence_not_found")
            return await self._build(session, evidence)

    async def list_packets(
        self,
        *,
        after_evidence_id: uuid.UUID | None = None,
        limit: int = 50,
        provider: str | None = None,
        quality: str | None = None,
        scan_limit: int | None = None,
    ) -> PacketPage:
        if not 1 <= limit <= self._max_batch:
            raise RichEvidenceError("rich_evidence_batch_limit_invalid")
        if provider is not None and provider not in {"marketaux", "finnhub", "eia", "sec_edgar"}:
            raise RichEvidenceError("rich_evidence_filter_invalid")
        if quality is not None and quality not in {"complete", "partial"}:
            raise RichEvidenceError("rich_evidence_filter_invalid")
        effective_scan_limit = scan_limit if scan_limit is not None else min(limit * 10, 500)
        if not limit <= effective_scan_limit <= 500:
            raise RichEvidenceError("rich_evidence_scan_limit_invalid")
        async with self._factory() as session, session.begin():
            await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            packets: list[RichEvidencePacket] = []
            scanned = 0
            cursor = after_evidence_id
            more = False
            while len(packets) < limit and scanned < effective_scan_limit:
                take = min(_PREFETCH_CHUNK_SIZE, effective_scan_limit - scanned)
                statement = (
                    select(EvidenceItem)
                    .join(EvidenceProjectionLink)
                    .where(EvidenceProjectionLink.status == EvidenceProjectionLinkStatus.LINKED)
                    .distinct()
                    .order_by(EvidenceItem.id)
                    .limit(take + 1)
                )
                if cursor is not None:
                    statement = statement.where(EvidenceItem.id > cursor)
                if provider is not None:
                    statement = statement.where(EvidenceItem.provider == provider)
                rows = list((await session.scalars(statement)).all())
                page_rows = rows[:take]
                more = len(rows) > take
                if not page_rows:
                    more = False
                    break
                inputs = await self._load_inputs(session, page_rows)
                for evidence in page_rows:
                    scanned += 1
                    cursor = evidence.id
                    packet = await self._build(session, evidence, inputs)
                    if quality is None or packet.quality == quality:
                        packets.append(packet)
                        if len(packets) == limit:
                            more = more or evidence is not page_rows[-1]
                            break
                if len(packets) == limit or not more:
                    break
            exhausted = scanned >= effective_scan_limit and more
            return PacketPage(
                tuple(packets),
                cursor if more or exhausted else None,
                scanned,
                len(packets),
                exhausted,
                more or exhausted,
            )

    async def _load_inputs(
        self, session: AsyncSession, evidences: Sequence[EvidenceItem]
    ) -> _PacketInputs:
        evidence_ids = tuple(item.id for item in evidences)
        result_rows = (
            await session.execute(
                select(
                    EvidenceProjectionLink,
                    SafeFactProjection,
                    RawItemObservation,
                    CollectionRun,
                    CollectionTarget,
                )
                .join(
                    SafeFactProjection,
                    SafeFactProjection.id == EvidenceProjectionLink.safe_fact_projection_id,
                )
                .join(
                    RawItemObservation,
                    RawItemObservation.id == SafeFactProjection.observation_id,
                )
                .join(CollectionRun, CollectionRun.id == RawItemObservation.collection_run_id)
                .outerjoin(CollectionTarget, CollectionTarget.id == RawItemObservation.target_id)
                .where(
                    EvidenceProjectionLink.evidence_item_id.in_(evidence_ids),
                    EvidenceProjectionLink.status == EvidenceProjectionLinkStatus.LINKED,
                    SafeFactProjection.processing_status == SafeProjectionProcessingStatus.READY,
                )
            )
        ).all()
        grouped: dict[uuid.UUID, list[Any]] = {identity: [] for identity in evidence_ids}
        for row in result_rows:
            grouped[row[0].evidence_item_id].append(row)
        raw_ids = {item.raw_item_id for item in evidences}
        source_ids = {item.source_id for item in evidences}
        account_ids = {
            item.source_account_id for item in evidences if item.source_account_id is not None
        }
        raws = {
            item.id: item
            for item in await session.scalars(select(RawItem).where(RawItem.id.in_(raw_ids)))
        }
        sources = {
            item.id: item
            for item in await session.scalars(select(Source).where(Source.id.in_(source_ids)))
        }
        accounts = {
            item.id: item
            for item in await session.scalars(
                select(SourceAccount).where(SourceAccount.id.in_(account_ids))
            )
        }
        first_run_ids = {item.collection_run_id for item in raws.values()}
        first_runs = {
            item.id: item
            for item in await session.scalars(
                select(CollectionRun).where(CollectionRun.id.in_(first_run_ids))
            )
        }
        content_ids = {
            identity
            for item in evidences
            for identity in (item.content_item_id,)
            if identity is not None
        }
        content_ids.update(
            row[0].content_item_id for row in result_rows if row[0].content_item_id is not None
        )
        contents = {
            item.id: item
            for item in await session.scalars(
                select(ContentItem).where(ContentItem.id.in_(content_ids))
            )
        }
        return _PacketInputs(
            {identity: tuple(rows) for identity, rows in grouped.items()},
            raws,
            sources,
            accounts,
            first_runs,
            contents,
        )

    async def _build(
        self,
        session: AsyncSession,
        evidence: EvidenceItem,
        inputs: _PacketInputs | None = None,
    ) -> RichEvidencePacket:
        loaded = inputs or await self._load_inputs(session, (evidence,))
        rows = loaded.rows.get(evidence.id, ())
        if not rows:
            raise RichEvidenceError("rich_evidence_linked_projection_missing")
        raw = loaded.raws.get(evidence.raw_item_id)
        source = loaded.sources.get(evidence.source_id)
        first_run = loaded.first_runs.get(raw.collection_run_id) if raw else None
        account = (
            loaded.accounts.get(evidence.source_account_id)
            if evidence.source_account_id is not None
            else None
        )
        if raw is None or source is None or first_run is None:
            raise RichEvidenceError("rich_evidence_provenance_invalid")
        if (
            raw.source_id != evidence.source_id
            or raw.source_account_id != evidence.source_account_id
            or source.access_method != evidence.provider
            or first_run.source_id != evidence.source_id
            or first_run.source_account_id != evidence.source_account_id
            or (evidence.source_account_id is not None and account is None)
            or (account is not None and account.source_id != evidence.source_id)
            or raw.retention_class != source.retention_class
        ):
            raise RichEvidenceError("rich_evidence_provenance_invalid")

        revisions: list[tuple[EvidenceRevision, CollectionRun, CollectionTarget | None]] = []
        operations: set[str] = set()
        for link, projection, observation, run, target in rows:
            self._validate_provenance(evidence, raw, link, projection, observation, run, target)
            operations.add(projection.operation_key)
            try:
                normalized, quality = normalize_and_classify_factual_payload(
                    projection.provider,
                    projection.operation_key,
                    projection.projection_schema_version,
                    projection.factual_payload,
                )
            except ProjectionContractError:
                raise RichEvidenceError("rich_evidence_projection_invalid") from None
            if (
                normalized != projection.factual_payload
                or canonical_projection_hash(normalized) != projection.projection_hash
                or quality != projection.quality_status.value
            ):
                raise RichEvidenceError("rich_evidence_projection_invalid")
            facts = _typed_facts(projection.provider, projection.operation_key, normalized)
            revisions.append(
                (
                    EvidenceRevision(
                        link_id=link.id,
                        projection_id=projection.id,
                        observation_id=observation.id,
                        projection_hash=projection.projection_hash,
                        projection_schema_version=projection.projection_schema_version,
                        operation_key=projection.operation_key,
                        provider_contract_version=observation.provider_contract_version,
                        collection_target_id=observation.target_id,
                        config_revision=observation.config_revision,
                        linked_at=link.linked_at,
                        observed_at=observation.observed_at,
                        quality=cast(Any, quality),
                        facts=facts,
                    ),
                    run,
                    target,
                )
            )
        if len(operations) != 1:
            raise RichEvidenceError("rich_evidence_operation_conflict")
        revisions.sort(key=lambda item: (item[0].observed_at, item[0].projection_id.hex))
        current, current_run, current_target = revisions[-1]
        current_policy = factual_operation_policy(
            evidence.provider, current.operation_key, current.provider_contract_version
        )
        if raw.retention_class not in current_policy.retention:
            raise RichEvidenceError("rich_evidence_retention_policy_invalid")
        canonical_rows = [
            revision
            for revision, _run, _target in revisions
            if next(row[0] for row in rows if row[0].id == revision.link_id).canonical_evidence
        ]
        if len(canonical_rows) != 1:
            raise RichEvidenceError("rich_evidence_canonical_adoption_invalid")
        canonical = canonical_rows[0]
        if (
            evidence.event_time is None
            or _utc(evidence.event_time) != _facts_published_at(canonical.facts)
            or _utc(evidence.observed_at) != _utc(canonical.observed_at)
        ):
            raise RichEvidenceError("rich_evidence_time_provenance_invalid")
        selected = revisions[-self._max_revisions :]
        reason = "revision_budget" if len(selected) < len(revisions) else "none"
        content = await self._content_reference(
            session,
            evidence,
            raw.retention_class,
            rows,
            tuple(item[0] for item in revisions),
            current.operation_key,
            current.provider_contract_version,
            loaded.contents,
        )
        missing, blocked = _availability(current.facts)
        packet = self._assemble(
            evidence,
            raw,
            current.operation_key,
            current,
            tuple(item[0] for item in selected),
            current_run,
            current_target,
            content,
            missing,
            blocked,
            len(revisions),
            reason,
        )
        while _encoded_size(packet) > self._max_bytes and len(packet.revisions) > 1:
            reason = "serialized_size_budget"
            packet = self._assemble(
                evidence,
                raw,
                current.operation_key,
                current,
                packet.revisions[1:],
                current_run,
                current_target,
                content,
                missing,
                blocked,
                len(revisions),
                reason,
            )
        if _encoded_size(packet) > self._max_bytes:
            raise RichEvidenceError("rich_evidence_packet_budget_exceeded")
        return packet

    @staticmethod
    def _validate_provenance(
        evidence: EvidenceItem,
        raw: RawItem,
        link: EvidenceProjectionLink,
        projection: SafeFactProjection,
        observation: RawItemObservation,
        run: CollectionRun,
        target: CollectionTarget | None,
    ) -> None:
        expected_type = _EXPECTED_TYPE.get((projection.provider, projection.operation_key))
        try:
            policy = factual_operation_policy(
                projection.provider,
                projection.operation_key,
                observation.provider_contract_version,
            )
        except ValueError:
            raise RichEvidenceError("rich_evidence_contract_version_invalid") from None
        legacy_allowed = (
            projection.provider,
            projection.operation_key,
        ) in LEGACY_OPAQUE_IDENTITY_OPERATIONS
        legacy_identity = (
            legacy_provider_item_identity(
                projection.provider, projection.operation_key, projection.factual_payload
            )
            if legacy_allowed
            else str(projection.factual_payload.get("provider_item_id"))
        )
        adopted_legacy = (
            evidence.provider_item_id == legacy_identity
            and evidence.provider_item_id != str(projection.factual_payload.get("provider_item_id"))
            and legacy_allowed
        )
        expected_access = "link_only" if adopted_legacy else policy.access
        is_market = projection.operation_key == "quote"
        expected_content = {
            "has_title": bool(projection.factual_payload.get("title")),
            "has_body": False,
            "has_url": bool(
                projection.factual_payload.get("canonical_url")
                or projection.factual_payload.get("official_url")
            ),
            "has_snippet": False,
            "has_description": False,
        }
        expected_numeric = {
            "has_numeric_value": is_market or projection.provider == "eia",
            "numeric_field_count": 7 if is_market else 1 if projection.provider == "eia" else 0,
            "nullable_allowed": projection.provider == "eia",
        }
        if (
            expected_type is None
            or expected_type != evidence.provider_item_type
            or policy.item_type != evidence.provider_item_type
            or policy.evidence_kind != evidence.evidence_kind
            or policy.source_type != evidence.source_type
            or expected_access != evidence.access_level
            or evidence.provider_item_id
            not in {str(projection.factual_payload.get("provider_item_id")), legacy_identity}
            or (
                legacy_allowed
                and legacy_identity != str(projection.factual_payload.get("provider_item_id"))
                and evidence.provider_item_id == legacy_identity
                and not adopted_legacy
            )
            or evidence.processing_status != "validated"
            or evidence.official_source_flag != (projection.provider in {"eia", "sec_edgar"})
            or evidence.market_data_flag != is_market
            or evidence.disclosure_flag != (projection.provider == "sec_edgar")
            or evidence.news_signal_flag
            != (projection.operation_key in {"news_all", "company_news"})
            or (link.canonical_evidence and evidence.content_presence != expected_content)
            or (link.canonical_evidence and evidence.numeric_presence != expected_numeric)
            or (
                link.canonical_evidence
                and not adopted_legacy
                and evidence.provider_item_hash != projection.projection_hash
            )
            or projection.provider != evidence.provider
            or projection.raw_item_id != raw.id
            or observation.raw_item_id != raw.id
            or observation.provider != projection.provider
            or observation.operation_key != projection.operation_key
            or observation.projection_hash != projection.projection_hash
            or observation.collection_run_id != run.id
            or run.target_id != observation.target_id
            or observation.source_id != raw.source_id
            or observation.source_account_id != raw.source_account_id
            or run.source_id != raw.source_id
            or run.source_account_id != raw.source_account_id
            or link.safe_fact_projection_id != projection.id
        ):
            raise RichEvidenceError("rich_evidence_provenance_invalid")
        if observation.target_id is not None and (
            target is None
            or target.id != observation.target_id
            or target.source_id != raw.source_id
            or target.source_account_id != raw.source_account_id
            or target.operation_key != observation.operation_key
        ):
            raise RichEvidenceError("rich_evidence_provenance_invalid")

    async def _content_reference(
        self,
        session: AsyncSession,
        evidence: EvidenceItem,
        retention_class: str,
        rows: Sequence[Any],
        revisions: tuple[EvidenceRevision, ...],
        operation: str,
        provider_contract_version: int,
        contents: Mapping[uuid.UUID, ContentItem],
    ) -> ContentReference:
        content_ids = {row[0].content_item_id for row in rows if row[0].content_item_id is not None}
        if evidence.content_item_id is not None:
            content_ids.add(evidence.content_item_id)
        if len(content_ids) > 1:
            raise RichEvidenceError("rich_evidence_content_conflict")
        if not content_ids:
            return ContentReference(None, None, False, "unavailable", False)
        content = contents.get(next(iter(content_ids)))
        canonical_content_links = [row[0] for row in rows if row[0].canonical_content]
        if len(canonical_content_links) != 1:
            raise RichEvidenceError("rich_evidence_content_adoption_invalid")
        canonical_content_link = canonical_content_links[0]
        canonical_content_revision = next(
            revision for revision in revisions if revision.link_id == canonical_content_link.id
        )
        try:
            policy = factual_operation_policy(
                evidence.provider, operation, provider_contract_version
            )
        except ValueError:
            raise RichEvidenceError("rich_evidence_contract_version_invalid") from None
        if (
            content is None
            or content.raw_item_id != evidence.raw_item_id
            or content.source_id != evidence.source_id
            or content.source_account_id != evidence.source_account_id
            or policy.content != content.content_kind.value
            or content.body_availability.value != "unavailable"
            or content.body is not None
            or content.source_summary is not None
            or content.author is not None
            or content.content_hash is not None
            or content.source_updated_at is not None
            or content.reply_to_external_id is not None
            or content.quote_external_id is not None
            or content.repost_external_id is not None
            or content.deleted_status.value != "unknown"
            or set(content.metadata_) - {"provider", "operation_key", "retention"}
            or content.metadata_.get("provider") != evidence.provider
            or content.metadata_.get("operation_key") != operation
            or content.metadata_.get("retention") != retention_class
        ):
            raise RichEvidenceError("rich_evidence_content_invalid")
        if evidence.provider in {"eia"} or (
            evidence.provider == "finnhub" and operation == "quote"
        ):
            raise RichEvidenceError("rich_evidence_content_invalid")
        facts = canonical_content_revision.facts
        if evidence.provider in {"marketaux", "finnhub"} and not (
            isinstance(facts, (MarketauxNewsFacts, FinnhubCompanyNewsFacts))
            and facts.title == content.title
            and facts.canonical_url == content.canonical_url
            and content.original_url == facts.canonical_url
            and content.source_published_at is not None
            and _utc(content.source_published_at) == _facts_published_at(facts)
            and content.language
            == (facts.language if isinstance(facts, MarketauxNewsFacts) else None)
        ):
            raise RichEvidenceError("rich_evidence_content_invalid")
        if evidence.provider == "sec_edgar" and not (
            isinstance(facts, SecFilingFacts)
            and facts.official_url == content.canonical_url
            and content.original_url == facts.official_url
            and content.source_published_at is not None
            and _utc(content.source_published_at) == _facts_published_at(facts)
            and content.language is None
        ):
            raise RichEvidenceError("rich_evidence_content_invalid")
        return ContentReference(
            content.id,
            content.content_kind.value,
            bool(content.title),
            content.body_availability.value,
            bool(content.canonical_url),
        )

    @staticmethod
    def _assemble(
        evidence: EvidenceItem,
        raw: RawItem,
        operation: str,
        current: EvidenceRevision,
        revisions: tuple[EvidenceRevision, ...],
        run: CollectionRun,
        target: CollectionTarget | None,
        content: ContentReference,
        missing: tuple[str, ...],
        blocked: tuple[str, ...],
        total: int,
        reason: str,
    ) -> RichEvidencePacket:
        truncation = PacketTruncation(
            len(revisions) < total, total, len(revisions), cast(Any, reason)
        )
        base = RichEvidencePacket(
            packet_version=1,
            packet_digest="",
            evidence_id=evidence.id,
            raw_item_id=raw.id,
            provider=evidence.provider,
            operation_key=operation,
            evidence_kind=evidence.evidence_kind,
            provider_item_type=evidence.provider_item_type,
            access_level=evidence.access_level,
            identity_mode=_identity_mode(evidence, current),
            retention_class=raw.retention_class,
            canonical_event_time=evidence.event_time,
            canonical_observed_at=evidence.observed_at,
            current_published_at=_facts_published_at(current.facts),
            current_observed_at=current.observed_at,
            provenance=SourceProvenance(
                source_id=evidence.source_id,
                source_account_id=evidence.source_account_id,
                collection_target_id=target.id if target else None,
                first_persistence_run_id=raw.collection_run_id,
                config_revision=current.config_revision,
                provider_contract_version=current.provider_contract_version,
            ),
            content=content,
            current=current,
            revisions=revisions,
            quality=current.quality,
            missing_fields=missing,
            blocked_fields=blocked,
            truncation=truncation,
        )
        digest = hashlib.sha256(_canonical_json(_material(base)).encode()).hexdigest()
        return dataclasses.replace(base, packet_digest=digest)


def _typed_facts(provider: str, operation: str, payload: Mapping[str, Any]) -> TypedFacts:
    data = dict(payload)
    if (provider, operation) == ("marketaux", "news_all"):
        if data["symbols"] is not None:
            data["symbols"] = tuple(data["symbols"])
        return MarketauxNewsFacts(**data)
    if (provider, operation) == ("finnhub", "quote"):
        return FinnhubQuoteFacts(**data)
    if (provider, operation) == ("finnhub", "company_news"):
        return FinnhubCompanyNewsFacts(**data)
    if (provider, operation) == ("eia", "electricity_retail_sales"):
        return EiaRetailFacts(**data)
    if (provider, operation) == ("eia", "electricity_rto_region_data"):
        return EiaRtoFacts(**data)
    if (provider, operation) == ("sec_edgar", "submissions_recent"):
        return SecFilingFacts(**data)
    raise RichEvidenceError("rich_evidence_operation_unknown")


def _identity_mode(
    evidence: EvidenceItem, current: EvidenceRevision
) -> Literal["canonical", "adopted_legacy_opaque"]:
    if (evidence.provider, current.operation_key) not in LEGACY_OPAQUE_IDENTITY_OPERATIONS:
        return "canonical"
    expected = legacy_provider_item_identity(
        evidence.provider, current.operation_key, dataclasses.asdict(current.facts)
    )
    return (
        "adopted_legacy_opaque"
        if evidence.provider_item_id == expected
        and evidence.provider_item_id != current.facts.provider_item_id
        else "canonical"
    )


def _availability(facts: TypedFacts) -> tuple[tuple[str, ...], tuple[str, ...]]:
    values = dataclasses.asdict(facts)
    missing = tuple(sorted(key for key, value in values.items() if value is None))
    blocked = tuple(
        sorted(key.removesuffix("_coverage") for key, value in values.items() if value == "blocked")
    )
    return missing, blocked


def _material(packet: RichEvidencePacket) -> dict[str, Any]:
    value = dataclasses.asdict(packet)
    value.pop("packet_digest", None)
    return cast(dict[str, Any], _json_safe(value))


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(child) for child in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return _utc(value).isoformat()
    return value


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def _encoded_size(packet: RichEvidencePacket) -> int:
    return len(canonical_packet_bytes(packet))


def canonical_packet_bytes(packet: RichEvidencePacket) -> bytes:
    """Serialize the complete packet exactly as counted by the byte budget."""
    return _canonical_json(cast(dict[str, Any], _json_safe(dataclasses.asdict(packet)))).encode()


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise RichEvidenceError("rich_evidence_time_provenance_invalid")
    return value.astimezone(UTC)


def _facts_published_at(facts: TypedFacts) -> datetime:
    try:
        value = datetime.fromisoformat(facts.published_at.replace("Z", "+00:00"))
    except ValueError:
        raise RichEvidenceError("rich_evidence_time_provenance_invalid") from None
    return _utc(value)
