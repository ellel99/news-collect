import asyncio
import os
import uuid

import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command
from market_intelligence.core.config import get_settings

_DISPOSABLE_URL: str | None = None
_ADMIN_URL: str | None = None


async def _database_ddl(url: str, statement: str) -> None:
    engine = create_async_engine(url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as connection:
            await connection.execute(text(statement))
    finally:
        await engine.dispose()


def pytest_sessionstart(session: pytest.Session) -> None:
    """Give every pytest process its own disposable PostgreSQL database."""
    del session
    global _ADMIN_URL, _DISPOSABLE_URL
    configured = os.environ.get("TEST_DATABASE_URL")
    if not configured:
        return
    parsed = make_url(configured)
    if parsed.get_backend_name() != "postgresql":
        raise pytest.UsageError("postgres_integration_database_required")
    token = uuid.uuid4().hex
    database = f"news_collect_test_{token}"
    # URL.__str__ intentionally redacts passwords as ``***``. Test database
    # creation needs a connectable URL while the value remains process-local
    # and is never logged.
    _ADMIN_URL = parsed.set(database=parsed.database).render_as_string(hide_password=False)
    _DISPOSABLE_URL = parsed.set(database=database).render_as_string(hide_password=False)
    asyncio.run(_database_ddl(_ADMIN_URL, f'CREATE DATABASE "{database}"'))
    os.environ["NEWS_COLLECT_TEST_ISOLATION_TOKEN"] = token
    os.environ["TEST_DATABASE_URL"] = _DISPOSABLE_URL
    os.environ["DATABASE_URL"] = _DISPOSABLE_URL
    get_settings.cache_clear()
    # Test database provisioning owns DBA prerequisites. Business migrations
    # deliberately never install extensions.
    asyncio.run(_database_ddl(_DISPOSABLE_URL, "CREATE EXTENSION pgcrypto"))
    command.upgrade(Config("alembic.ini"), "head")


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    del session, exitstatus
    if _DISPOSABLE_URL is None or _ADMIN_URL is None:
        return
    database = make_url(_DISPOSABLE_URL).database
    assert database is not None and database.startswith("news_collect_test_")
    asyncio.run(
        _database_ddl(
            _ADMIN_URL,
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            f"WHERE datname='{database}' AND pid<>pg_backend_pid()",
        )
    )
    asyncio.run(_database_ddl(_ADMIN_URL, f'DROP DATABASE "{database}"'))


@pytest.fixture(autouse=True)
def clear_settings_cache() -> None:
    get_settings.cache_clear()
