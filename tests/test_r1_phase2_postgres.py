from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from market_intelligence.collection.phase2_migration import LegacyTargetPhase2Service
from market_intelligence.collection.target_configs import build_operation_registry
from market_intelligence.db.models import (
    AuditLog,
    AuthorizationStatus,
    CollectionCursor,
    CollectionRun,
    CollectionRunStatus,
    CollectionTarget,
    IdentityStatus,
    RawItem,
    Source,
    SourceAccount,
    SourceType,
)

POSTGRES_TEST_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://market_intelligence:local_dev_only@localhost:5432/market_intelligence",
)


@pytest.mark.asyncio
async def test_phase2_is_explicit_deterministic_idempotent_and_value_free() -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    marker = uuid.uuid4().hex
    source_id = account_id = run_id = cursor_id = None
    try:
        async with factory.begin() as session:
            source = Source(
                code=f"r1-phase2-{marker}",
                name="R1 Phase2",
                source_type=SourceType.API,
                access_method="marketaux",
                authorization_status=AuthorizationStatus.AUTHORIZED,
                retention_class="metadata_only",
                enabled=True,
            )
            session.add(source)
            await session.flush()
            account = SourceAccount(
                source_id=source.id,
                identity_status=IdentityStatus.VERIFIED,
                enabled=True,
                collection_options={"query": "technology", "language": "en"},
            )
            session.add(account)
            await session.flush()
            run = CollectionRun(
                source_id=source.id,
                source_account_id=account.id,
                started_at=datetime.now(UTC),
                status=CollectionRunStatus.SUCCEEDED,
            )
            cursor = CollectionCursor(
                source_account_id=account.id,
                cursor_type="provider_cursor_v1",
            )
            session.add_all((run, cursor))
            await session.flush()
            source_id, account_id, run_id, cursor_id = source.id, account.id, run.id, cursor.id

        service = LegacyTargetPhase2Service(factory, build_operation_registry())
        first = await service.run()
        second = await service.run()
        assert first.created_count >= 1
        assert first.runs_backfilled >= 1
        assert first.cursors_backfilled >= 1
        assert second.existing_count >= 1
        assert second.runs_backfilled == 0
        assert second.cursors_backfilled == 0

        async with factory() as session:
            target = await session.scalar(
                select(CollectionTarget).where(CollectionTarget.source_account_id == account_id)
            )
            assert target is not None
            assert target.target_key == f"legacy.marketaux.account.{account_id}"
            assert target.legacy_cursor_type == "provider_cursor_v1"
            assert target.status.value == "paused"
            assert (
                await session.scalar(
                    select(CollectionRun.target_id).where(CollectionRun.id == run_id)
                )
                == target.id
            )
            assert (
                await session.scalar(
                    select(CollectionCursor.target_id).where(CollectionCursor.id == cursor_id)
                )
                == target.id
            )
            audits = tuple(
                await session.scalars(
                    select(AuditLog).where(
                        AuditLog.action.in_(
                            ("r1_phase2_target_created", "r1_phase2_reconciliation")
                        )
                    )
                )
            )
            assert audits
            rendered = repr([(item.before, item.after) for item in audits]).lower()
            assert "technology" not in rendered
            assert "api_key" not in rendered
    finally:
        if source_id is not None:
            async with factory.begin() as session:
                await session.execute(
                    delete(AuditLog).where(
                        AuditLog.action.in_(
                            ("r1_phase2_target_created", "r1_phase2_reconciliation")
                        )
                    )
                )
                await session.execute(delete(RawItem).where(RawItem.source_id == source_id))
                await session.execute(
                    delete(CollectionCursor).where(CollectionCursor.id == cursor_id)
                )
                await session.execute(delete(CollectionRun).where(CollectionRun.id == run_id))
                await session.execute(
                    delete(CollectionTarget).where(CollectionTarget.source_id == source_id)
                )
                await session.execute(delete(SourceAccount).where(SourceAccount.id == account_id))
                await session.execute(delete(Source).where(Source.id == source_id))
        await engine.dispose()


@pytest.mark.asyncio
async def test_phase2_unknown_mapping_is_blocked_without_legacy_identity() -> None:
    engine = create_async_engine(POSTGRES_TEST_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    marker = uuid.uuid4().hex
    source_id = account_id = None
    try:
        async with factory.begin() as session:
            source = Source(
                code=f"r1-unknown-{marker}",
                name="Unknown",
                source_type=SourceType.API,
                access_method="unsupported_provider",
                authorization_status=AuthorizationStatus.AUTHORIZED,
                retention_class="metadata_only",
                enabled=True,
            )
            session.add(source)
            await session.flush()
            account = SourceAccount(
                source_id=source.id,
                identity_status=IdentityStatus.VERIFIED,
                enabled=True,
                collection_options={},
            )
            session.add(account)
            await session.flush()
            source_id, account_id = source.id, account.id

        report = await LegacyTargetPhase2Service(factory, build_operation_registry()).run()
        assert report.blocked_count >= 1
        async with factory() as session:
            target = await session.scalar(
                select(CollectionTarget).where(CollectionTarget.source_id == source_id)
            )
            assert target is not None
            assert target.status.value == "blocked"
            assert target.legacy_cursor_type is None
    finally:
        if source_id is not None:
            async with factory.begin() as session:
                await session.execute(delete(AuditLog).where(AuditLog.action.like("r1_phase2%")))
                await session.execute(
                    delete(CollectionTarget).where(CollectionTarget.source_id == source_id)
                )
                await session.execute(delete(SourceAccount).where(SourceAccount.id == account_id))
                await session.execute(delete(Source).where(Source.id == source_id))
        await engine.dispose()
