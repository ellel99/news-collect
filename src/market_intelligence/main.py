from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from redis.asyncio import Redis

from market_intelligence.api.health import router as health_router
from market_intelligence.core.config import Settings, get_settings
from market_intelligence.core.logging import CORRELATION_ID, bind_correlation_id, configure_logging
from market_intelligence.db.session import create_engine


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()
    configure_logging(resolved.APP_LOG_LEVEL)
    engine = create_engine(resolved)
    redis = Redis.from_url(resolved.REDIS_URL, decode_responses=True)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        await app.state.redis.aclose()
        await app.state.db_engine.dispose()

    app = FastAPI(title="Market Intelligence Collector", lifespan=lifespan)
    app.state.db_engine = engine
    app.state.redis = redis
    app.state.health_timeout = resolved.HEALTH_CHECK_TIMEOUT_SECONDS

    @app.middleware("http")
    async def correlation_id_middleware(request: Request, call_next: object) -> object:
        correlation_id = request.headers.get("X-Correlation-ID")
        token = bind_correlation_id(correlation_id)
        try:
            response = await call_next(request)  # type: ignore[operator]
            response.headers["X-Correlation-ID"] = CORRELATION_ID.get()
            return response
        finally:
            CORRELATION_ID.reset(token)

    app.include_router(health_router)
    return app


app = create_app()
