import pytest
from pydantic import ValidationError

from market_intelligence.core.config import Settings


def test_development_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.APP_ENV == "development"
    assert settings.APP_PORT == 8000


def test_invalid_port_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(APP_PORT=0, _env_file=None)


def test_invalid_database_scheme_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(DATABASE_URL="postgresql://host/db", _env_file=None)


def test_sqlite_database_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(DATABASE_URL="sqlite+aiosqlite:///:memory:", _env_file=None)


def test_production_defaults_fail_fast() -> None:
    with pytest.raises(ValidationError, match="production service URLs"):
        Settings(APP_ENV="production", _env_file=None)


def test_safe_projection_runtime_settings_are_bounded() -> None:
    settings = Settings(_env_file=None)
    assert settings.SAFE_PROJECTION_BATCH_LIMIT == 100
    assert settings.SAFE_PROJECTION_RECONCILE_INTERVAL_SECONDS == 60
    with pytest.raises(ValidationError):
        Settings(SAFE_PROJECTION_BATCH_LIMIT=501, _env_file=None)
    with pytest.raises(ValidationError):
        Settings(SAFE_PROJECTION_RECONCILE_INTERVAL_SECONDS=1, _env_file=None)
    assert settings.EVIDENCE_HANDOFF_BATCH_LIMIT == 100
    assert settings.EVIDENCE_HANDOFF_RECONCILE_INTERVAL_SECONDS == 60
    with pytest.raises(ValidationError):
        Settings(EVIDENCE_HANDOFF_BATCH_LIMIT=501, _env_file=None)
