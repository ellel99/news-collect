"""Fail-closed isolation guard for destructive PostgreSQL integration tests."""

from __future__ import annotations

from sqlalchemy.engine import make_url


def isolated_test_database_url(value: str | None) -> str:
    """Accept only an explicit PostgreSQL database whose name is test-only."""
    if not value:
        raise ValueError("test_database_url_required")
    url = make_url(value)
    database = (url.database or "").lower()
    if url.get_backend_name() != "postgresql":
        raise ValueError("test_database_backend_unsafe")
    if "test" not in database:
        raise ValueError("test_database_name_unsafe")
    return value
