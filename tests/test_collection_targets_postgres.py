from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

POSTGRES_TEST_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://market_intelligence:local_dev_only@localhost:5432/market_intelligence",
)


async def _source_account(connection):  # type: ignore[no-untyped-def]
    code = f"r1-{uuid.uuid4().hex}"
    source_id = await connection.scalar(
        text(
            "INSERT INTO sources(code,name,source_type,access_method,authorization_status,"
            "retention_class,enabled) VALUES "
            "(:code,'R1','api','marketaux','authorized','metadata_only',true) RETURNING id"
        ),
        {"code": code},
    )
    account_id = await connection.scalar(
        text(
            "INSERT INTO source_accounts(source_id,identity_status,enabled,collection_options) "
            "VALUES (:source,'verified',true,'{}'::jsonb) RETURNING id"
        ),
        {"source": source_id},
    )
    return source_id, account_id


async def _target(
    connection,
    source_id,
    account_id,
    *,
    key: str,
    status: str = "paused",
    legacy: str | None = "provider_cursor_v1",
):  # type: ignore[no-untyped-def]
    return await connection.scalar(
        text("""
        INSERT INTO collection_targets(
          target_key,source_id,source_account_id,operation_key,legacy_cursor_type,
          operation_config_version,provider_contract_version,operation_config,status,
          cadence_seconds,batch_limit,max_response_bytes,request_timeout_seconds,max_runtime_seconds,
          cursor_strategy,collection_mode,backfill_policy,revision_policy,rate_limit_group,
          next_due_at,health_status
        ) VALUES (
          :key,:source,:account,'news_all',:legacy,1,1,'{"query":"technology"}'::jsonb,:status,
          300,1,1000000,10,60,'compound','incremental','disabled','ignore','marketaux:default',
          :now,'unknown'
        ) RETURNING id
        """),
        {
            "key": key,
            "source": source_id,
            "account": account_id,
            "legacy": legacy,
            "status": status,
            "now": datetime.now(UTC),
        },
    )


@pytest.mark.asyncio
async def test_migration_a_guards_identity_activation_and_provenance() -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    try:
        async with engine.connect() as connection, connection.begin():
            source_id, account_id = await _source_account(connection)
            target_id = await _target(
                connection, source_id, account_id, key=f"r1.{uuid.uuid4().hex}"
            )
            with pytest.raises(DBAPIError, match="collection_target_identity_immutable"):
                await connection.execute(
                    text("UPDATE collection_targets SET legacy_cursor_type='other' WHERE id=:id"),
                    {"id": target_id},
                )
            await connection.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_active_requires_legacy_identity_and_owner_is_exclusive() -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            source_id, account_id = await _source_account(connection)
            first = await _target(
                connection,
                source_id,
                account_id,
                key=f"r1.{uuid.uuid4().hex}",
                status="active",
            )
            assert first is not None
            with pytest.raises(IntegrityError):
                await _target(
                    connection,
                    source_id,
                    account_id,
                    key=f"r1.{uuid.uuid4().hex}",
                    status="active",
                )
            await transaction.rollback()
            transaction = await connection.begin()
            source_id, account_id = await _source_account(connection)
            with pytest.raises(DBAPIError, match="active_target_requires_legacy_identity"):
                await _target(
                    connection,
                    source_id,
                    account_id,
                    key=f"r1.{uuid.uuid4().hex}",
                    status="active",
                    legacy=None,
                )
            await transaction.rollback()
            transaction = await connection.begin()
            source_id, account_id = await _source_account(connection)
            await _target(connection, source_id, account_id, key=f"r1.{uuid.uuid4().hex}")
            with pytest.raises(IntegrityError):
                await _target(connection, source_id, account_id, key=f"r1.{uuid.uuid4().hex}")
            await transaction.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_rollback_owner_is_retained_across_lifecycle_and_revision() -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    try:
        async with engine.connect() as connection, connection.begin():
            source_id, account_id = await _source_account(connection)
            owner = await _target(connection, source_id, account_id, key=f"r1.{uuid.uuid4().hex}")
            for status in ("paused", "blocked", "retired"):
                retired_at = datetime.now(UTC) if status == "retired" else None
                await connection.execute(
                    text("""
                    UPDATE collection_targets SET status=:status, retired_at=:retired,
                      config_revision=config_revision+1 WHERE id=:id
                    """),
                    {"status": status, "retired": retired_at, "id": owner},
                )
                with pytest.raises(IntegrityError):
                    async with connection.begin_nested():
                        await _target(
                            connection, source_id, account_id, key=f"r1.{uuid.uuid4().hex}"
                        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "unsafe_config",
    (
        '{"api_key":"redacted"}',
        '{"query":"https://example.invalid/path"}',
        '{"nested":{"module":"provider.dynamic"}}',
        '{"query":"authorization"}',
    ),
)
async def test_operation_config_database_guard_rejects_unsafe_manual_sql(
    unsafe_config: str,
) -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            source_id, account_id = await _source_account(connection)
            with pytest.raises(DBAPIError, match="collection_target_operation_config_unsafe"):
                await connection.execute(
                    text("""
                    INSERT INTO collection_targets(
                      target_key,source_id,source_account_id,operation_key,legacy_cursor_type,
                      operation_config_version,provider_contract_version,operation_config,status,
                      cadence_seconds,batch_limit,max_response_bytes,request_timeout_seconds,
                      max_runtime_seconds,cursor_strategy,collection_mode,backfill_policy,
                      revision_policy,rate_limit_group,next_due_at,health_status
                    ) VALUES (
                      :key,:source,:account,'news_all','provider_cursor_v1',1,1,
                      CAST(:config AS jsonb),'paused',300,1,1000000,10,60,'compound',
                      'incremental','disabled','ignore','marketaux:default',:now,'unknown'
                    )
                    """),
                    {
                        "key": f"r1.{uuid.uuid4().hex}",
                        "source": source_id,
                        "account": account_id,
                        "config": unsafe_config,
                        "now": datetime.now(UTC),
                    },
                )
            await transaction.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_referenced_source_provider_and_run_raw_provenance_are_immutable() -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            source_id, account_id = await _source_account(connection)
            target_id = await _target(
                connection, source_id, account_id, key=f"r1.{uuid.uuid4().hex}"
            )
            with pytest.raises(DBAPIError, match="source_access_method_immutable"):
                await connection.execute(
                    text("UPDATE sources SET access_method='finnhub' WHERE id=:id"),
                    {"id": source_id},
                )
            await transaction.rollback()
            transaction = await connection.begin()
            source_id, account_id = await _source_account(connection)
            target_id = await _target(
                connection, source_id, account_id, key=f"r1.{uuid.uuid4().hex}"
            )
            other_source, _ = await _source_account(connection)
            with pytest.raises(DBAPIError, match="collection_run_target_provenance_mismatch"):
                await connection.execute(
                    text(
                        "INSERT INTO collection_runs("
                        "target_id,run_mode,source_id,source_account_id,started_at,status"
                        ") VALUES "
                        "(:target,'normal',:source,:account,:now,'running')"
                    ),
                    {
                        "target": target_id,
                        "source": other_source,
                        "account": account_id,
                        "now": datetime.now(UTC),
                    },
                )
            await transaction.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_migration_a_downgrade_fails_closed_when_target_state_exists() -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            source_id, account_id = await _source_account(connection)
            await _target(connection, source_id, account_id, key=f"r1.{uuid.uuid4().hex}")

            def attempt(sync_connection: object) -> None:
                revision = ScriptDirectory.from_config(Config("alembic.ini")).get_revision("0006")
                assert revision is not None
                with Operations.context(MigrationContext.configure(sync_connection)):
                    revision.module.downgrade()

            with pytest.raises(RuntimeError, match="migration_a_downgrade_unsafe_target_state"):
                await connection.run_sync(attempt)
            await transaction.rollback()
    finally:
        await engine.dispose()
