import pytest

from market_intelligence.test_database import isolated_test_database_url


def test_database_guard_requires_explicit_url() -> None:
    with pytest.raises(ValueError, match="test_database_url_required"):
        isolated_test_database_url(None)


def test_database_guard_rejects_development_database() -> None:
    with pytest.raises(ValueError, match="test_database_name_unsafe"):
        isolated_test_database_url("postgresql+asyncpg://u:p@localhost/market_intelligence")


def test_database_guard_accepts_explicit_test_database() -> None:
    value = "postgresql+asyncpg://u:p@localhost/market_intelligence_test"
    assert isolated_test_database_url(value) == value
