import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from market_intelligence.db import (
    Base,
    ContentItem,
    EvidenceItem,
    Notification,
    OutboxMessage,
    RawItem,
)
from market_intelligence.evidence.contracts import (
    EVIDENCE_VERSION,
    AccessLevel,
    CommonEvidenceEnvelope,
    EvidenceKind,
    NumericPresence,
    ProcessingStatus,
    Provider,
    ProviderItemType,
    SourceType,
)
from market_intelligence.evidence.provider_mappings import map_finnhub_quote_to_evidence
from market_intelligence.evidence.write_path import (
    EvidenceWriteRequest,
    EvidenceWriteService,
    EvidenceWriteStatus,
)

SECRET = "never-echo-this-value"
POSTGRES_TEST_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://market_intelligence:local_dev_only@localhost:5432/market_intelligence",
)


@pytest_asyncio.fixture
async def write_session() -> AsyncIterator[AsyncSession]:
    schema = f"spec_0023_write_{uuid.uuid4().hex}"
    engine = create_async_engine(
        POSTGRES_TEST_URL,
        connect_args={"server_settings": {"search_path": schema}},
    )
    async with engine.connect() as setup_connection:
        await setup_connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        await setup_connection.commit()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            yield session
    finally:
        async with engine.connect() as cleanup_connection:
            await cleanup_connection.rollback()
            await cleanup_connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            await cleanup_connection.commit()
        await engine.dispose()


async def _provenance(
    session: AsyncSession,
    *,
    source_id: uuid.UUID | None = None,
    with_account: bool = False,
    with_content: bool = False,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    if source_id is None:
        source_id = (
            await session.execute(
                text(
                    """
                    INSERT INTO sources (
                        code, name, source_type, access_method, authorization_status,
                        retention_class
                    ) VALUES (
                        :code, 'Write Path Fixture', 'api', 'fake', 'authorized',
                        'metadata_only'
                    ) RETURNING id
                    """
                ),
                {"code": f"write-path-{uuid.uuid4().hex}"},
            )
        ).scalar_one()
    account_id = None
    if with_account:
        account_id = (
            await session.execute(
                text(
                    """
                    INSERT INTO source_accounts (
                        source_id, external_id, identity_status, enabled, collection_options
                    ) VALUES (
                        :source_id, :external_id, 'verified', true, '{}'::jsonb
                    ) RETURNING id
                    """
                ),
                {"source_id": source_id, "external_id": f"acct-{uuid.uuid4().hex}"},
            )
        ).scalar_one()
    run_id = (
        await session.execute(
            text(
                """
                INSERT INTO collection_runs (
                    source_id, source_account_id, started_at, status
                ) VALUES (
                    :source_id, :account_id, :now, 'succeeded'
                ) RETURNING id
                """
            ),
            {"source_id": source_id, "account_id": account_id, "now": now},
        )
    ).scalar_one()
    raw_item_id = (
        await session.execute(
            text(
                """
                INSERT INTO raw_items (
                    source_id, source_account_id, collection_run_id, fetched_at,
                    retention_class, parse_status
                ) VALUES (
                    :source_id, :account_id, :run_id, :now, 'metadata_only', 'parsed'
                ) RETURNING id
                """
            ),
            {
                "source_id": source_id,
                "account_id": account_id,
                "run_id": run_id,
                "now": now,
            },
        )
    ).scalar_one()
    content_item_id = None
    if with_content:
        content_item_id = (
            await session.execute(
                text(
                    """
                    INSERT INTO content_items (
                        raw_item_id, source_id, source_account_id, content_kind,
                        body_availability, first_seen_at, deleted_status, metadata
                    ) VALUES (
                        :raw_item_id, :source_id, :account_id, 'article', 'unavailable',
                        :now, 'unknown', '{}'::jsonb
                    ) RETURNING id
                    """
                ),
                {
                    "raw_item_id": raw_item_id,
                    "source_id": source_id,
                    "account_id": account_id,
                    "now": now,
                },
            )
        ).scalar_one()
    await session.flush()
    return {
        "source_id": source_id,
        "source_account_id": account_id,
        "raw_item_id": raw_item_id,
        "content_item_id": content_item_id,
    }


def _envelope(
    *,
    item_hash: str = "a" * 64,
    item_id: str | None = "opaque-provider-item",
    raw_reference: str | None = "internal://safe/reference",
) -> CommonEvidenceEnvelope:
    now = datetime.now(UTC)
    return CommonEvidenceEnvelope(
        evidence_version=EVIDENCE_VERSION,
        provider=Provider.FINNHUB,
        provider_item_type=ProviderItemType.FINNHUB_QUOTE,
        source_type=SourceType.MARKET_DATA,
        source_priority=None,
        access_level=AccessLevel.LINK_ONLY,
        provider_item_id=item_id,
        provider_item_hash=item_hash,
        canonical_source_reference=None,
        observed_at=now,
        event_time=now,
        numeric_presence=NumericPresence(
            has_numeric_value=True,
            numeric_field_count=1,
        ),
        evidence_kind=EvidenceKind.MARKET_DATA,
        market_data_flag=True,
        raw_payload_reference=raw_reference,
        processing_status=ProcessingStatus.VALIDATED,
    )


def _request(
    provenance: dict[str, Any],
    envelope: CommonEvidenceEnvelope | None = None,
) -> EvidenceWriteRequest:
    return EvidenceWriteRequest(envelope=envelope or _envelope(), **provenance)


async def _count(session: AsyncSession, model: type[Any]) -> int:
    return int((await session.scalar(select(func.count()).select_from(model))) or 0)


@pytest.mark.asyncio
async def test_valid_envelope_writes_only_evidence_item(write_session: AsyncSession) -> None:
    provenance = await _provenance(write_session, with_content=True)
    before = {
        RawItem: await _count(write_session, RawItem),
        ContentItem: await _count(write_session, ContentItem),
        Notification: await _count(write_session, Notification),
        OutboxMessage: await _count(write_session, OutboxMessage),
    }

    outcome = await EvidenceWriteService(write_session).write_one(_request(provenance))

    assert outcome.status is EvidenceWriteStatus.INSERTED
    assert outcome.evidence_item_id is not None
    assert await _count(write_session, EvidenceItem) == 1
    assert {model: await _count(write_session, model) for model in before} == before


@pytest.mark.asyncio
async def test_provider_mapping_output_is_accepted_as_input(
    write_session: AsyncSession,
) -> None:
    provenance = await _provenance(write_session)
    envelope = map_finnhub_quote_to_evidence(
        {"c": 1.0, "t": 1_700_000_000},
        {
            "symbol": "SYNTHETIC",
            "observed_at": datetime(2026, 1, 1, tzinfo=UTC),
        },
    )
    outcome = await EvidenceWriteService(write_session).write_one(_request(provenance, envelope))
    assert outcome.status is EvidenceWriteStatus.INSERTED
    assert await _count(write_session, EvidenceItem) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reference",
    [
        f"https://example.invalid/{SECRET}",
        f"http://example.invalid/{SECRET}",
        f"internal://capture/api_key={SECRET}",
        f"capture://provider/API_TOKEN={SECRET}",
        f"local-ref://provider/ToKeN={SECRET}",
        f"internal://AUTHORIZATION={SECRET}",
        f"internal://X-Finnhub-Token={SECRET}",
    ],
)
async def test_unsafe_reference_is_removed_and_blocked(
    write_session: AsyncSession, reference: str
) -> None:
    provenance = await _provenance(write_session)
    outcome = await EvidenceWriteService(write_session).write_one(
        _request(provenance, _envelope(raw_reference=reference))
    )

    assert outcome.status is EvidenceWriteStatus.BLOCKED
    assert [error.code for error in outcome.errors] == ["raw_payload_reference_unsafe"]
    stored = await write_session.scalar(select(EvidenceItem))
    assert stored is not None
    assert stored.raw_payload_reference is None
    assert stored.processing_status == "blocked"
    assert stored.errors == [
        {
            "code": "raw_payload_reference_unsafe",
            "field": "raw_payload_reference",
            "safe_message": "unsafe_reference_removed",
        }
    ]
    serialized = repr((outcome, stored.errors))
    assert SECRET not in serialized
    assert reference not in serialized


@pytest.mark.asyncio
async def test_missing_raw_item_returns_safe_reference_not_found(
    write_session: AsyncSession,
) -> None:
    provenance = await _provenance(write_session)
    provenance["raw_item_id"] = uuid.uuid4()
    outcome = await EvidenceWriteService(write_session).write_one(_request(provenance))
    assert outcome.status is EvidenceWriteStatus.INVALID
    assert [error.code for error in outcome.errors] == ["reference_not_found"]
    assert await _count(write_session, EvidenceItem) == 0


@pytest.mark.asyncio
async def test_raw_source_provenance_mismatch_is_rejected(
    write_session: AsyncSession,
) -> None:
    first = await _provenance(write_session)
    second = await _provenance(write_session)
    request = _request({**first, "raw_item_id": second["raw_item_id"]})
    outcome = await EvidenceWriteService(write_session).write_one(request)
    assert outcome.status is EvidenceWriteStatus.INVALID
    assert [error.code for error in outcome.errors] == ["provenance_mismatch"]
    assert await _count(write_session, EvidenceItem) == 0


@pytest.mark.asyncio
async def test_source_account_provenance_mismatch_is_rejected(
    write_session: AsyncSession,
) -> None:
    first = await _provenance(write_session, with_account=True)
    second = await _provenance(write_session, with_account=True)
    request = _request({**first, "source_account_id": second["source_account_id"]})
    outcome = await EvidenceWriteService(write_session).write_one(request)
    assert outcome.status is EvidenceWriteStatus.INVALID
    assert [error.code for error in outcome.errors] == ["provenance_mismatch"]


@pytest.mark.asyncio
async def test_content_raw_provenance_mismatch_is_rejected(
    write_session: AsyncSession,
) -> None:
    first = await _provenance(write_session, with_content=True)
    second = await _provenance(write_session, source_id=first["source_id"])
    request = _request({**second, "content_item_id": first["content_item_id"]})
    outcome = await EvidenceWriteService(write_session).write_one(request)
    assert outcome.status is EvidenceWriteStatus.INVALID
    assert [error.code for error in outcome.errors] == ["provenance_mismatch"]


@pytest.mark.asyncio
async def test_provider_hash_duplicate_returns_existing(write_session: AsyncSession) -> None:
    provenance = await _provenance(write_session)
    service = EvidenceWriteService(write_session)
    envelope = _envelope()
    first = await service.write_one(_request(provenance, envelope))
    duplicate = await service.write_one(_request(provenance, envelope))
    assert first.status is EvidenceWriteStatus.INSERTED
    assert duplicate.status is EvidenceWriteStatus.EXISTING
    assert duplicate.evidence_item_id == first.evidence_item_id
    assert await _count(write_session, EvidenceItem) == 1


@pytest.mark.asyncio
async def test_provider_hash_conflicting_provenance_fails(write_session: AsyncSession) -> None:
    first = await _provenance(write_session)
    second = await _provenance(write_session)
    service = EvidenceWriteService(write_session)
    await service.write_one(_request(first))
    conflict = await service.write_one(_request(second))
    assert conflict.status is EvidenceWriteStatus.FAILED
    assert [error.code for error in conflict.errors] == ["provider_hash_conflict"]
    assert await _count(write_session, EvidenceItem) == 1


@pytest.mark.asyncio
async def test_provider_item_id_duplicate_returns_existing(write_session: AsyncSession) -> None:
    provenance = await _provenance(write_session)
    envelope = _envelope(item_hash="b" * 64, item_id="opaque-id")
    service = EvidenceWriteService(write_session)
    await service.write_one(_request(provenance, envelope))
    duplicate = await service.write_one(_request(provenance, envelope))
    assert duplicate.status is EvidenceWriteStatus.EXISTING


@pytest.mark.asyncio
async def test_provider_item_id_hash_conflict_fails(write_session: AsyncSession) -> None:
    provenance = await _provenance(write_session)
    service = EvidenceWriteService(write_session)
    await service.write_one(
        _request(provenance, _envelope(item_hash="b" * 64, item_id="opaque-id"))
    )
    conflict = await service.write_one(
        _request(provenance, _envelope(item_hash="c" * 64, item_id="opaque-id"))
    )
    assert conflict.status is EvidenceWriteStatus.FAILED
    assert [error.code for error in conflict.errors] == ["provider_item_id_conflict"]
    assert await _count(write_session, EvidenceItem) == 1


@pytest.mark.asyncio
async def test_nullable_provider_item_id_is_not_synthesized(write_session: AsyncSession) -> None:
    provenance = await _provenance(write_session)
    outcome = await EvidenceWriteService(write_session).write_one(
        _request(provenance, _envelope(item_id=None))
    )
    stored = await write_session.scalar(select(EvidenceItem))
    assert outcome.status is EvidenceWriteStatus.INSERTED
    assert stored is not None
    assert stored.provider_item_id is None


@pytest.mark.asyncio
async def test_batch_savepoint_preserves_success_and_count_conservation(
    write_session: AsyncSession,
) -> None:
    provenance = await _provenance(write_session)
    invalid = {**provenance, "raw_item_id": uuid.uuid4()}
    valid = _request(
        provenance,
        _envelope(item_hash="d" * 64, item_id="second-item"),
    )
    summary = await EvidenceWriteService(write_session).write_many([_request(invalid), valid])
    assert summary.input_count == 2
    assert summary.inserted_count == 1
    assert summary.invalid_count == 1
    assert summary.duplicate_count == 0
    assert summary.blocked_count == 0
    assert summary.failed_count == 0
    assert (
        sum(
            (
                summary.inserted_count,
                summary.duplicate_count,
                summary.blocked_count,
                summary.invalid_count,
                summary.failed_count,
            )
        )
        == summary.input_count
    )
    assert await _count(write_session, EvidenceItem) == 1


@pytest.mark.asyncio
async def test_database_error_is_redacted_and_next_batch_item_succeeds(
    write_session: AsyncSession,
) -> None:
    provenance = await _provenance(write_session)
    oversized = _request(
        provenance,
        _envelope(item_hash="e" * 64, item_id="oversized", raw_reference="internal://" + "x" * 600),
    )
    valid = _request(
        provenance,
        _envelope(item_hash="f" * 64, item_id="valid-after-failure"),
    )
    summary = await EvidenceWriteService(write_session).write_many([oversized, valid])
    assert summary.failed_count == 1
    assert summary.inserted_count == 1
    assert await _count(write_session, EvidenceItem) == 1
    serialized = repr(summary)
    assert "x" * 100 not in serialized
    assert "parameters" not in serialized.lower()


def test_write_path_source_has_no_forbidden_runtime_dependencies() -> None:
    source = (
        Path(__file__).parents[1] / "src/market_intelligence/evidence/write_path.py"
    ).read_text(encoding="utf-8")
    for forbidden in (
        "requests",
        "httpx",
        "urllib",
        "provider_capture",
        "local_evaluation",
        "Adapter",
        "CollectionRunner",
        "Event",
        "OpenAI",
        "Telegram",
        "Recommendation",
        "Portfolio",
        "Holding",
    ):
        assert forbidden not in source


def test_outcome_and_summary_types_do_not_expose_content_or_values() -> None:
    from market_intelligence.evidence.write_path import (
        EvidenceWriteError,
        EvidenceWriteOutcome,
        EvidenceWriteSummary,
    )

    forbidden_fields = {
        "title",
        "body",
        "url",
        "snippet",
        "description",
        "quote_value",
        "eia_value",
        "accession_number",
        "primary_document",
        "raw_payload",
        "sql_parameters",
    }
    assert forbidden_fields.isdisjoint(EvidenceWriteError.__dataclass_fields__)
    assert forbidden_fields.isdisjoint(EvidenceWriteOutcome.__dataclass_fields__)
    assert forbidden_fields.isdisjoint(EvidenceWriteSummary.__dataclass_fields__)
