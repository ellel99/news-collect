"""Deterministic, bounded and read-only Rich Evidence Packet construction."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, cast

from sqlalchemy import select
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
        async with self._factory() as session:
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
    ) -> PacketPage:
        if not 1 <= limit <= self._max_batch:
            raise RichEvidenceError("rich_evidence_batch_limit_invalid")
        if provider is not None and provider not in {"marketaux", "finnhub", "eia", "sec_edgar"}:
            raise RichEvidenceError("rich_evidence_filter_invalid")
        if quality is not None and quality not in {"complete", "partial"}:
            raise RichEvidenceError("rich_evidence_filter_invalid")
        async with self._factory() as session:
            statement = (
                select(EvidenceItem)
                .join(EvidenceProjectionLink)
                .where(EvidenceProjectionLink.status == EvidenceProjectionLinkStatus.LINKED)
                .distinct()
                .order_by(EvidenceItem.id)
                .limit(limit + 1)
            )
            if after_evidence_id is not None:
                statement = statement.where(EvidenceItem.id > after_evidence_id)
            if provider is not None:
                statement = statement.where(EvidenceItem.provider == provider)
            rows = list((await session.scalars(statement)).all())
            packets: list[RichEvidencePacket] = []
            for evidence in rows[:limit]:
                packet = await self._build(session, evidence)
                if quality is None or packet.quality == quality:
                    packets.append(packet)
            next_id = rows[limit - 1].id if len(rows) > limit else None
            return PacketPage(tuple(packets), next_id)

    async def _build(self, session: AsyncSession, evidence: EvidenceItem) -> RichEvidencePacket:
        rows = (
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
                    EvidenceProjectionLink.evidence_item_id == evidence.id,
                    EvidenceProjectionLink.status == EvidenceProjectionLinkStatus.LINKED,
                    SafeFactProjection.processing_status == SafeProjectionProcessingStatus.READY,
                )
            )
        ).all()
        if not rows:
            raise RichEvidenceError("rich_evidence_linked_projection_missing")
        raw = await session.get(RawItem, evidence.raw_item_id)
        source = await session.get(Source, evidence.source_id)
        if raw is None or source is None:
            raise RichEvidenceError("rich_evidence_provenance_invalid")
        if (
            raw.source_id != evidence.source_id
            or raw.source_account_id != evidence.source_account_id
            or source.access_method != evidence.provider
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
        selected = revisions[-self._max_revisions :]
        reason = "revision_budget" if len(selected) < len(revisions) else "none"
        content = await self._content_reference(
            session, evidence, rows, current.operation_key, current.provider_contract_version
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
        if (
            expected_type is None
            or expected_type != evidence.provider_item_type
            or policy.item_type != evidence.provider_item_type
            or policy.evidence_kind != evidence.evidence_kind
            or policy.source_type != evidence.source_type
            or policy.access != evidence.access_level
            or projection.provider != evidence.provider
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
            or link.safe_fact_projection_id != projection.id
        ):
            raise RichEvidenceError("rich_evidence_provenance_invalid")
        if observation.target_id is not None and (
            target is None
            or target.id != observation.target_id
            or target.source_id != raw.source_id
            or target.source_account_id != raw.source_account_id
            or target.operation_key != observation.operation_key
            or target.provider_contract_version != observation.provider_contract_version
        ):
            raise RichEvidenceError("rich_evidence_provenance_invalid")

    async def _content_reference(
        self,
        session: AsyncSession,
        evidence: EvidenceItem,
        rows: Sequence[Any],
        operation: str,
        provider_contract_version: int,
    ) -> ContentReference:
        content_ids = {row[0].content_item_id for row in rows if row[0].content_item_id is not None}
        if evidence.content_item_id is not None:
            content_ids.add(evidence.content_item_id)
        if len(content_ids) > 1:
            raise RichEvidenceError("rich_evidence_content_conflict")
        if not content_ids:
            return ContentReference(None, None, False, "unavailable", False)
        content = await session.get(ContentItem, next(iter(content_ids)))
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
            or (
                evidence.provider == "sec_edgar"
                and content.body_availability.value != "unavailable"
            )
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
            retention_class=raw.retention_class,
            event_time=evidence.event_time,
            observed_at=evidence.observed_at,
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
        return value.isoformat()
    return value


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )


def _encoded_size(packet: RichEvidencePacket) -> int:
    return len(_canonical_json(_material(packet)).encode())
