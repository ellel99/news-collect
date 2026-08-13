import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from market_intelligence.db import Base

POSTGRES_TEST_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://market_intelligence:local_dev_only@localhost:5432/market_intelligence",
)


@pytest_asyncio.fixture
async def postgres_connection() -> AsyncIterator[AsyncConnection]:
    schema = f"spec_0002_test_{uuid.uuid4().hex}"
    engine = create_async_engine(
        POSTGRES_TEST_URL,
        connect_args={"server_settings": {"search_path": schema}},
    )
    async with engine.connect() as setup_connection:
        await setup_connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        await setup_connection.commit()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        async with engine.connect() as connection:
            yield connection
    finally:
        async with engine.connect() as cleanup_connection:
            await cleanup_connection.rollback()
            await cleanup_connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            await cleanup_connection.commit()
        await engine.dispose()


@pytest.mark.asyncio
async def test_postgres_schema_allowlist(postgres_connection: AsyncConnection) -> None:
    result = await postgres_connection.execute(
        text(
            """
            SELECT tablename
            FROM pg_tables
            WHERE schemaname = current_schema()
            """
        )
    )
    assert set(result.scalars()) == {
        "system_metadata",
        "sources",
        "source_accounts",
        "collection_cursors",
        "collection_runs",
        "raw_items",
        "content_items",
        "notifications",
        "outbox_messages",
        "audit_logs",
        "evidence_items",
        "event_candidates",
        "event_candidate_evidence",
        "impact_analyses",
    }


@pytest.mark.asyncio
async def test_migrated_public_schema_allowlist() -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT tablename
                    FROM pg_tables
                    WHERE schemaname = 'public'
                    """
                )
            )
        assert set(result.scalars()) == {
            "alembic_version",
            "system_metadata",
            "sources",
            "source_accounts",
            "collection_cursors",
            "collection_runs",
            "raw_items",
            "content_items",
            "notifications",
            "outbox_messages",
            "audit_logs",
            "evidence_items",
            "event_candidates",
            "event_candidate_evidence",
            "impact_analyses",
        }
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_raw_item_requires_valid_collection_run(
    postgres_connection: AsyncConnection,
) -> None:
    now = datetime.now(UTC)
    source_id = (
        await postgres_connection.execute(
            text(
                """
                INSERT INTO sources (
                    code, name, source_type, access_method, authorization_status,
                    retention_class
                )
                VALUES (
                    'fixture-source', 'Fixture Source', 'rss', 'unknown', 'access_tbd',
                    'metadata_only'
                )
                RETURNING id
                """
            )
        )
    ).scalar_one()
    run_id = (
        await postgres_connection.execute(
            text(
                """
                INSERT INTO collection_runs (source_id, started_at, status)
                VALUES (:source_id, :started_at, 'running')
                RETURNING id
                """
            ),
            {"source_id": source_id, "started_at": now},
        )
    ).scalar_one()
    raw_id = (
        await postgres_connection.execute(
            text(
                """
                INSERT INTO raw_items (
                    source_id, collection_run_id, fetched_at, retention_class, parse_status
                )
                VALUES (:source_id, :run_id, :fetched_at, 'metadata_only', 'pending')
                RETURNING id
                """
            ),
            {"source_id": source_id, "run_id": run_id, "fetched_at": now},
        )
    ).scalar_one()
    assert raw_id is not None
    await postgres_connection.commit()

    with pytest.raises(IntegrityError):
        await postgres_connection.execute(
            text(
                """
                INSERT INTO raw_items (
                    source_id, collection_run_id, fetched_at, retention_class, parse_status
                )
                VALUES (:source_id, :run_id, :fetched_at, 'metadata_only', 'pending')
                """
            ),
            {"source_id": source_id, "run_id": uuid.uuid4(), "fetched_at": now},
        )
    await postgres_connection.rollback()


@pytest.mark.asyncio
async def test_outbox_idempotency_key_rejects_duplicates(
    postgres_connection: AsyncConnection,
) -> None:
    values = {
        "idempotency_key": "fixture-message-v1",
        "aggregate_id": uuid.uuid4(),
        "available_at": datetime.now(UTC),
    }
    await postgres_connection.execute(
        text(
            """
            INSERT INTO outbox_messages (
                idempotency_key, aggregate_type, aggregate_id, message_type,
                status, available_at
            )
            VALUES (
                :idempotency_key, 'content_item', :aggregate_id, 'fixture.created',
                'pending', :available_at
            )
            """
        ),
        values,
    )
    await postgres_connection.commit()

    with pytest.raises(IntegrityError):
        await postgres_connection.execute(
            text(
                """
                INSERT INTO outbox_messages (
                    idempotency_key, aggregate_type, aggregate_id, message_type,
                    status, available_at
                )
                VALUES (
                    :idempotency_key, 'content_item', :aggregate_id, 'fixture.created',
                    'pending', :available_at
                )
                """
            ),
            values,
        )
    await postgres_connection.rollback()
