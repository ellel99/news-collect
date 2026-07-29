from unittest.mock import AsyncMock

import httpx
import pytest

from market_intelligence.core.config import Settings
from market_intelligence.main import create_app


class Connection:
    async def __aenter__(self) -> "Connection":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def execute(self, statement: object) -> None:
        del statement


class Engine:
    def connect(self) -> Connection:
        return Connection()

    async def dispose(self) -> None:
        return None


@pytest.fixture
def app() -> object:
    application = create_app(Settings(APP_ENV="test", _env_file=None))
    application.state.db_engine = Engine()
    application.state.redis = AsyncMock()
    application.state.redis.ping.return_value = True
    return application


@pytest.mark.asyncio
async def test_live_does_not_check_dependencies(app: object) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_ready_reports_healthy_dependencies(app: object) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/health/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_ready_reports_database_failure_without_details(app: object) -> None:
    app.state.db_engine.connect = lambda: (_ for _ in ()).throw(OSError("secret DB URL"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["components"]["database"] == {
        "status": "error",
        "error_code": "DB_UNAVAILABLE",
    }
    assert "secret DB URL" not in response.text


@pytest.mark.asyncio
async def test_ready_reports_redis_failure(app: object) -> None:
    app.state.redis.ping.side_effect = OSError("redis password")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["components"]["redis"]["error_code"] == "REDIS_UNAVAILABLE"
