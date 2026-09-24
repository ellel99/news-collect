"""Fail-closed isolation guard for destructive PostgreSQL integration tests."""

from __future__ import annotations

import os

from sqlalchemy.engine import make_url


def isolated_test_database_url(value: str | None, *, isolation_token: str | None = None) -> str:
    """Accept only an explicit PostgreSQL database whose name is test-only."""
    if not value:
        raise ValueError("test_database_url_required")
    url = make_url(value)
    database = (url.database or "").lower()
    host = (url.host or "").lower()
    username = (url.username or "").lower()
    query = dict(url.query)
    if url.get_backend_name() != "postgresql":
        raise ValueError("test_database_backend_unsafe")
    if database != "market_intelligence_test" and not database.startswith("news_collect_test_"):
        raise ValueError("test_database_name_unsafe")
    if username != "market_intelligence":
        raise ValueError("test_database_user_unsafe")
    if any(key in query for key in {"options", "search_path", "server_settings"}):
        raise ValueError("test_database_schema_unsafe")
    token = isolation_token or os.environ.get("NEWS_COLLECT_TEST_ISOLATION_TOKEN")
    if database.startswith("news_collect_test_") and token != database.removeprefix(
        "news_collect_test_"
    ):
        raise ValueError("test_database_isolation_token_mismatch")
    if host not in {"localhost", "127.0.0.1", "postgres"}:
        if database == "market_intelligence_test":
            raise ValueError("test_database_remote_disposable_database_required")
        if not token:
            raise ValueError("test_database_remote_isolation_token_required")
    return value
