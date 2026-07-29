import asyncio
from typing import Protocol

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

router = APIRouter(prefix="/health", tags=["health"])


class ComponentStatus(BaseModel):
    status: str
    error_code: str | None = None


class ReadinessResponse(BaseModel):
    status: str
    components: dict[str, ComponentStatus]


class HealthDependencies(Protocol):
    db_engine: AsyncEngine
    redis: Redis
    health_timeout: float


@router.get("/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


async def check_database(engine: AsyncEngine, timeout: float) -> ComponentStatus:
    try:
        async with asyncio.timeout(timeout):
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        return ComponentStatus(status="ok")
    except Exception:
        return ComponentStatus(status="error", error_code="DB_UNAVAILABLE")


async def check_redis(redis: Redis, timeout: float) -> ComponentStatus:
    try:
        async with asyncio.timeout(timeout):
            await redis.ping()
        return ComponentStatus(status="ok")
    except Exception:
        return ComponentStatus(status="error", error_code="REDIS_UNAVAILABLE")


@router.get("/ready", response_model=ReadinessResponse)
async def ready(request: Request, response: Response) -> ReadinessResponse:
    dependencies: HealthDependencies = request.app.state
    database, redis = await asyncio.gather(
        check_database(dependencies.db_engine, dependencies.health_timeout),
        check_redis(dependencies.redis, dependencies.health_timeout),
    )
    components = {"database": database, "redis": redis}
    is_ready = all(component.status == "ok" for component in components.values())
    if not is_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(status="ok" if is_ready else "error", components=components)
