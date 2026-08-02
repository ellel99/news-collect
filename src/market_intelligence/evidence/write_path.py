"""Safe persistence boundary for normalized evidence envelopes."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from market_intelligence.db.models import (
    ContentItem,
    EvidenceItem,
    RawItem,
    SourceAccount,
)
from market_intelligence.evidence.contracts import (
    CommonEvidenceEnvelope,
    ProcessingStatus,
    Provider,
    validate_evidence_envelope,
    validate_raw_payload_reference,
)


class EvidenceWriteStatus(StrEnum):
    INSERTED = "inserted"
    EXISTING = "existing"
    DUPLICATE = "duplicate"
    BLOCKED = "blocked"
    INVALID = "invalid"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class EvidenceWriteError:
    code: str
    field: str | None
    safe_message: str
    opaque_reference: str | None = None


@dataclass(frozen=True, slots=True)
class EvidenceWriteRequest:
    envelope: CommonEvidenceEnvelope
    source_id: uuid.UUID
    raw_item_id: uuid.UUID
    source_account_id: uuid.UUID | None = None
    content_item_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class EvidenceWriteOutcome:
    status: EvidenceWriteStatus
    evidence_item_id: uuid.UUID | None
    provider: str
    provider_item_hash: str
    provider_item_id: str | None
    raw_item_id: uuid.UUID
    errors: tuple[EvidenceWriteError, ...] = ()


@dataclass(frozen=True, slots=True)
class EvidenceWriteSummary:
    input_count: int
    inserted_count: int
    duplicate_count: int
    blocked_count: int
    invalid_count: int
    failed_count: int
    outcomes: tuple[EvidenceWriteOutcome, ...]
    safe_errors: tuple[EvidenceWriteError, ...]

    def __post_init__(self) -> None:
        accounted = (
            self.inserted_count
            + self.duplicate_count
            + self.blocked_count
            + self.invalid_count
            + self.failed_count
        )
        if self.input_count != accounted:
            raise ValueError("evidence_write_summary_count_mismatch")


@dataclass(frozen=True, slots=True)
class _RawProvenance:
    source_id: uuid.UUID
    source_account_id: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class _ContentProvenance:
    source_id: uuid.UUID
    source_account_id: uuid.UUID | None
    raw_item_id: uuid.UUID


class EvidenceWriteRepository:
    """Database queries used by the evidence write service."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def raw_provenance(self, raw_item_id: uuid.UUID) -> _RawProvenance | None:
        row = (
            await self._session.execute(
                select(RawItem.source_id, RawItem.source_account_id).where(
                    RawItem.id == raw_item_id
                )
            )
        ).one_or_none()
        return None if row is None else _RawProvenance(*row)

    async def account_source_id(self, account_id: uuid.UUID) -> uuid.UUID | None:
        return cast(
            uuid.UUID | None,
            await self._session.scalar(
                select(SourceAccount.source_id).where(SourceAccount.id == account_id)
            ),
        )

    async def content_provenance(self, content_item_id: uuid.UUID) -> _ContentProvenance | None:
        row = (
            await self._session.execute(
                select(
                    ContentItem.source_id,
                    ContentItem.source_account_id,
                    ContentItem.raw_item_id,
                ).where(ContentItem.id == content_item_id)
            )
        ).one_or_none()
        return None if row is None else _ContentProvenance(*row)

    async def by_provider_hash(self, provider: str, item_hash: str) -> EvidenceItem | None:
        return cast(
            EvidenceItem | None,
            await self._session.scalar(
                select(EvidenceItem).where(
                    EvidenceItem.provider == provider,
                    EvidenceItem.provider_item_hash == item_hash,
                )
            ),
        )

    async def by_provider_item_id(self, provider: str, item_id: str | None) -> EvidenceItem | None:
        if item_id is None:
            return None
        return cast(
            EvidenceItem | None,
            await self._session.scalar(
                select(EvidenceItem).where(
                    EvidenceItem.provider == provider,
                    EvidenceItem.provider_item_id == item_id,
                )
            ),
        )

    def add(self, item: EvidenceItem) -> None:
        self._session.add(item)

    async def flush(self) -> None:
        await self._session.flush()


class EvidenceWriteService:
    """Validate and persist evidence rows without owning the outer transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repository = EvidenceWriteRepository(session)

    async def write_one(self, request: EvidenceWriteRequest) -> EvidenceWriteOutcome:
        reference_errors = validate_raw_payload_reference(request.envelope.raw_payload_reference)
        raw_reference = request.envelope.raw_payload_reference
        processing_status = _enum_value(request.envelope.processing_status)
        stored_errors = [asdict(error) for error in request.envelope.errors]
        write_errors: tuple[EvidenceWriteError, ...] = ()
        if reference_errors:
            raw_reference = None
            processing_status = ProcessingStatus.BLOCKED.value
            unsafe_error = EvidenceWriteError(
                code="raw_payload_reference_unsafe",
                field="raw_payload_reference",
                safe_message="unsafe_reference_removed",
            )
            write_errors = (unsafe_error,)
            stored_errors.append(_stored_error(unsafe_error))

        validation_errors = validate_evidence_envelope(
            _validated_copy(request.envelope, raw_reference, processing_status)
        )
        if validation_errors:
            return _request_outcome(
                request,
                EvidenceWriteStatus.INVALID,
                errors=(
                    EvidenceWriteError(
                        code="envelope_invalid",
                        field=validation_errors[0].field,
                        safe_message="envelope_validation_failed",
                    ),
                ),
            )

        provenance_error = await self._validate_provenance(request)
        if provenance_error is not None:
            return _request_outcome(
                request,
                EvidenceWriteStatus.INVALID,
                errors=(provenance_error,),
            )

        values = _evidence_values(
            request,
            raw_reference=raw_reference,
            processing_status=processing_status,
            stored_errors=stored_errors,
        )
        conflict = await self._classify_existing(request, values)
        if conflict is not None:
            return conflict

        item = EvidenceItem(**values)
        try:
            async with self._session.begin_nested():
                self._repository.add(item)
                await self._repository.flush()
        except IntegrityError:
            concurrent = await self._classify_existing(request, values)
            if concurrent is not None:
                return concurrent
            return _request_outcome(
                request,
                EvidenceWriteStatus.FAILED,
                errors=(
                    EvidenceWriteError(
                        code="constraint_rejected",
                        field=None,
                        safe_message="database_constraint_rejected",
                    ),
                ),
            )
        except SQLAlchemyError:
            return _request_outcome(
                request,
                EvidenceWriteStatus.FAILED,
                errors=(
                    EvidenceWriteError(
                        code="database_write_failed",
                        field=None,
                        safe_message="database_write_failed",
                    ),
                ),
            )

        return _request_outcome(
            request,
            (
                EvidenceWriteStatus.BLOCKED
                if processing_status == ProcessingStatus.BLOCKED.value
                else EvidenceWriteStatus.INSERTED
            ),
            evidence_item_id=item.id,
            errors=write_errors,
        )

    async def write_many(self, items: Sequence[EvidenceWriteRequest]) -> EvidenceWriteSummary:
        outcomes: list[EvidenceWriteOutcome] = []
        for request in items:
            try:
                outcomes.append(await self.write_one(request))
            except Exception:  # safety boundary: never expose database/input details
                outcomes.append(
                    EvidenceWriteOutcome(
                        status=EvidenceWriteStatus.FAILED,
                        evidence_item_id=None,
                        provider=_safe_provider(request.envelope.provider),
                        provider_item_hash=request.envelope.provider_item_hash or "",
                        provider_item_id=request.envelope.provider_item_id,
                        raw_item_id=request.raw_item_id,
                        errors=(
                            EvidenceWriteError(
                                code="database_write_failed",
                                field=None,
                                safe_message="database_write_failed",
                            ),
                        ),
                    )
                )
        return _summarize(outcomes)

    async def _validate_provenance(
        self, request: EvidenceWriteRequest
    ) -> EvidenceWriteError | None:
        raw = await self._repository.raw_provenance(request.raw_item_id)
        if raw is None:
            return EvidenceWriteError(
                code="reference_not_found",
                field="raw_item_id",
                safe_message="reference_not_found",
                opaque_reference=str(request.raw_item_id),
            )
        if raw.source_id != request.source_id or raw.source_account_id != request.source_account_id:
            return _provenance_mismatch("raw_item_id", request.raw_item_id)

        if request.source_account_id is not None:
            account_source_id = await self._repository.account_source_id(request.source_account_id)
            if account_source_id is None:
                return EvidenceWriteError(
                    code="reference_not_found",
                    field="source_account_id",
                    safe_message="reference_not_found",
                    opaque_reference=str(request.source_account_id),
                )
            if account_source_id != request.source_id:
                return _provenance_mismatch("source_account_id", request.source_account_id)

        if request.content_item_id is not None:
            content = await self._repository.content_provenance(request.content_item_id)
            if content is None:
                return EvidenceWriteError(
                    code="reference_not_found",
                    field="content_item_id",
                    safe_message="reference_not_found",
                    opaque_reference=str(request.content_item_id),
                )
            if (
                content.source_id != request.source_id
                or content.source_account_id != request.source_account_id
                or content.raw_item_id != request.raw_item_id
            ):
                return _provenance_mismatch("content_item_id", request.content_item_id)
        return None

    async def _classify_existing(
        self, request: EvidenceWriteRequest, values: dict[str, Any]
    ) -> EvidenceWriteOutcome | None:
        provider = values["provider"]
        item_hash = values["provider_item_hash"]
        by_hash = await self._repository.by_provider_hash(provider, item_hash)
        if by_hash is not None:
            if _equivalent(by_hash, values):
                return _existing_outcome(request, by_hash)
            return _conflict_outcome(request, "provider_hash_conflict")

        item_id = values["provider_item_id"]
        by_id = await self._repository.by_provider_item_id(provider, item_id)
        if by_id is not None:
            if by_id.provider_item_hash != item_hash:
                return _conflict_outcome(request, "provider_item_id_conflict")
            if _equivalent(by_id, values):
                return _existing_outcome(request, by_id)
            return _conflict_outcome(request, "provider_hash_conflict")
        return None


def _validated_copy(
    envelope: CommonEvidenceEnvelope,
    raw_reference: str | None,
    processing_status: str,
) -> CommonEvidenceEnvelope:
    from dataclasses import replace

    return replace(
        envelope,
        raw_payload_reference=raw_reference,
        processing_status=processing_status,
    )


def _enum_value(value: StrEnum | str) -> str:
    return value.value if isinstance(value, StrEnum) else value


def _safe_provider(value: Provider | str) -> str:
    try:
        return Provider(value).value
    except (TypeError, ValueError):
        return "unknown"


def _request_outcome(
    request: EvidenceWriteRequest,
    status: EvidenceWriteStatus,
    *,
    evidence_item_id: uuid.UUID | None = None,
    errors: tuple[EvidenceWriteError, ...] = (),
) -> EvidenceWriteOutcome:
    return EvidenceWriteOutcome(
        status=status,
        evidence_item_id=evidence_item_id,
        provider=_safe_provider(request.envelope.provider),
        provider_item_hash=request.envelope.provider_item_hash or "",
        provider_item_id=request.envelope.provider_item_id,
        raw_item_id=request.raw_item_id,
        errors=errors,
    )


def _stored_error(error: EvidenceWriteError) -> dict[str, str | None]:
    return {"code": error.code, "field": error.field, "safe_message": error.safe_message}


def _evidence_values(
    request: EvidenceWriteRequest,
    *,
    raw_reference: str | None,
    processing_status: str,
    stored_errors: list[dict[str, Any]],
) -> dict[str, Any]:
    envelope = request.envelope
    return {
        "evidence_version": envelope.evidence_version,
        "provider": _enum_value(envelope.provider),
        "provider_item_type": _enum_value(envelope.provider_item_type),
        "evidence_kind": _enum_value(envelope.evidence_kind),
        "source_type": _enum_value(envelope.source_type),
        "source_id": request.source_id,
        "source_account_id": request.source_account_id,
        "raw_item_id": request.raw_item_id,
        "content_item_id": request.content_item_id,
        "provider_item_id": envelope.provider_item_id,
        "provider_item_hash": envelope.provider_item_hash,
        "event_time": envelope.event_time,
        "observed_at": envelope.observed_at,
        "access_level": _enum_value(envelope.access_level),
        "processing_status": processing_status,
        "official_source_flag": envelope.official_source_flag,
        "market_data_flag": envelope.market_data_flag,
        "disclosure_flag": envelope.disclosure_flag,
        "news_signal_flag": envelope.news_signal_flag,
        "content_presence": asdict(envelope.content_presence),
        "numeric_presence": asdict(envelope.numeric_presence),
        "entity_refs": list(envelope.entity_refs),
        "asset_refs": list(envelope.asset_refs),
        "topic_refs": list(envelope.topic_refs),
        "raw_payload_reference": raw_reference,
        "errors": stored_errors,
    }


_EQUIVALENCE_FIELDS = (
    "evidence_version",
    "provider",
    "provider_item_type",
    "evidence_kind",
    "source_type",
    "source_id",
    "source_account_id",
    "raw_item_id",
    "content_item_id",
    "provider_item_id",
    "provider_item_hash",
    "event_time",
    "access_level",
    "processing_status",
    "official_source_flag",
    "market_data_flag",
    "disclosure_flag",
    "news_signal_flag",
    "content_presence",
    "numeric_presence",
    "entity_refs",
    "asset_refs",
    "topic_refs",
    "raw_payload_reference",
    "errors",
)


def _equivalent(item: EvidenceItem, values: dict[str, Any]) -> bool:
    return all(getattr(item, field) == values[field] for field in _EQUIVALENCE_FIELDS)


def _existing_outcome(request: EvidenceWriteRequest, item: EvidenceItem) -> EvidenceWriteOutcome:
    return EvidenceWriteOutcome(
        status=EvidenceWriteStatus.EXISTING,
        evidence_item_id=item.id,
        provider=item.provider,
        provider_item_hash=item.provider_item_hash,
        provider_item_id=item.provider_item_id,
        raw_item_id=request.raw_item_id,
    )


def _conflict_outcome(request: EvidenceWriteRequest, code: str) -> EvidenceWriteOutcome:
    return EvidenceWriteOutcome(
        status=EvidenceWriteStatus.FAILED,
        evidence_item_id=None,
        provider=_safe_provider(request.envelope.provider),
        provider_item_hash=request.envelope.provider_item_hash or "",
        provider_item_id=request.envelope.provider_item_id,
        raw_item_id=request.raw_item_id,
        errors=(
            EvidenceWriteError(
                code=code,
                field=(
                    "provider_item_hash" if code == "provider_hash_conflict" else "provider_item_id"
                ),
                safe_message="provider_identity_conflict",
            ),
        ),
    )


def _provenance_mismatch(field: str, reference: uuid.UUID) -> EvidenceWriteError:
    return EvidenceWriteError(
        code="provenance_mismatch",
        field=field,
        safe_message="provenance_check_failed",
        opaque_reference=str(reference),
    )


def _summarize(outcomes: Sequence[EvidenceWriteOutcome]) -> EvidenceWriteSummary:
    def count(*statuses: EvidenceWriteStatus) -> int:
        return sum(outcome.status in statuses for outcome in outcomes)

    return EvidenceWriteSummary(
        input_count=len(outcomes),
        inserted_count=count(EvidenceWriteStatus.INSERTED),
        duplicate_count=count(EvidenceWriteStatus.EXISTING, EvidenceWriteStatus.DUPLICATE),
        blocked_count=count(EvidenceWriteStatus.BLOCKED),
        invalid_count=count(EvidenceWriteStatus.INVALID),
        failed_count=count(EvidenceWriteStatus.FAILED),
        outcomes=tuple(outcomes),
        safe_errors=tuple(error for outcome in outcomes for error in outcome.errors),
    )
