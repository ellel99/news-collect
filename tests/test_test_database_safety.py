import pytest

from market_intelligence.test_database import isolated_test_database_url


def test_database_guard_requires_explicit_url() -> None:
    with pytest.raises(ValueError, match="test_database_url_required"):
        isolated_test_database_url(None)


def test_database_guard_rejects_development_database() -> None:
    with pytest.raises(ValueError, match="test_database_name_unsafe"):
        isolated_test_database_url("postgresql+asyncpg://u:p@localhost/market_intelligence")


def test_database_guard_accepts_explicit_test_database() -> None:
    value = "postgresql+asyncpg://market_intelligence:p@localhost/market_intelligence_test"
    assert isolated_test_database_url(value) == value


@pytest.mark.parametrize("database", ["contest", "latest", "prod_test", "mytest"])
def test_database_guard_rejects_ambiguous_test_names(database: str) -> None:
    with pytest.raises(ValueError, match="test_database_name_unsafe"):
        isolated_test_database_url(
            f"postgresql+asyncpg://market_intelligence:p@localhost/{database}"
        )


def test_database_guard_rejects_unapproved_user() -> None:
    with pytest.raises(ValueError, match="test_database_user_unsafe"):
        isolated_test_database_url("postgresql+asyncpg://prod:p@localhost/market_intelligence_test")


def test_database_guard_requires_token_for_remote_host() -> None:
    value = "postgresql+asyncpg://market_intelligence:p@db.example/market_intelligence_test"
    with pytest.raises(ValueError, match="test_database_remote_disposable_database_required"):
        isolated_test_database_url(value)
    with pytest.raises(ValueError, match="test_database_remote_disposable_database_required"):
        isolated_test_database_url(value, isolation_token="ephemeral-ci-token")


def test_database_guard_rejects_caller_selected_schema() -> None:
    value = (
        "postgresql+asyncpg://market_intelligence:p@localhost/market_intelligence_test"
        "?options=-csearch_path%3Dpublic"
    )
    with pytest.raises(ValueError, match="test_database_schema_unsafe"):
        isolated_test_database_url(value)


def test_disposable_database_token_must_match_random_database_identity() -> None:
    value = "postgresql+asyncpg://market_intelligence:p@localhost/news_collect_test_abc123"
    with pytest.raises(ValueError, match="test_database_isolation_token_mismatch"):
        isolated_test_database_url(value, isolation_token="reused-token")
    assert isolated_test_database_url(value, isolation_token="abc123") == value
