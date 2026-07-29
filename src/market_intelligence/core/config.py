from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    APP_ENV: Literal["development", "test", "production"] = "development"
    APP_LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    APP_HOST: str = "127.0.0.1"
    APP_PORT: int = Field(default=8000, ge=1, le=65535)
    DATABASE_URL: str = (
        "postgresql+asyncpg://market_intelligence:local_dev_only@localhost:5432/market_intelligence"
    )
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/2"
    HEALTH_CHECK_TIMEOUT_SECONDS: float = Field(default=2.0, gt=0, le=30)
    COLLECTION_DISPATCH_INTERVAL_SECONDS: int = Field(default=30, ge=5, le=3600)
    COLLECTION_STALE_RUN_SCAN_SECONDS: int = Field(default=300, ge=30, le=86400)
    COLLECTION_STALE_RUN_AFTER_SECONDS: int = Field(default=900, ge=1)
    COLLECTION_ADAPTER_TIMEOUT_SECONDS: int = Field(default=30, ge=1, le=300)
    COLLECTION_TASK_DEADLINE_SECONDS: int = Field(default=120, ge=1)
    COLLECTION_BATCH_LIMIT: int = Field(default=100, ge=1, le=1000)
    COLLECTION_MAX_RETRIES: int = Field(default=3, ge=0, le=10)
    COLLECTION_RETRY_BASE_SECONDS: int = Field(default=5, ge=1, le=3600)
    COLLECTION_RETRY_MAX_SECONDS: int = Field(default=300, ge=1)
    COLLECTION_MAX_RETRY_AFTER_SECONDS: int = Field(default=900, ge=1, le=86400)
    COLLECTION_LOCK_TTL_SECONDS: int = Field(default=180, ge=1)

    @field_validator("DATABASE_URL")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        if not value.startswith("postgresql+asyncpg://"):
            raise ValueError("must use a PostgreSQL asyncpg SQLAlchemy URL")
        return value

    @field_validator("REDIS_URL", "CELERY_BROKER_URL", "CELERY_RESULT_BACKEND")
    @classmethod
    def validate_redis_url(cls, value: str) -> str:
        if not value.startswith(("redis://", "rediss://")):
            raise ValueError("must be a Redis URL")
        return value

    @field_validator("DATABASE_URL", "REDIS_URL", "CELERY_BROKER_URL", "CELERY_RESULT_BACKEND")
    @classmethod
    def require_non_default_production_credentials(cls, value: str, info: object) -> str:
        del info
        return value

    def model_post_init(self, __context: object) -> None:
        if self.APP_ENV == "production":
            insecure_markers = ("local_dev_only", "localhost", "@postgres:")
            urls = (
                self.DATABASE_URL,
                self.REDIS_URL,
                self.CELERY_BROKER_URL,
                self.CELERY_RESULT_BACKEND,
            )
            if any(marker in url for marker in insecure_markers for url in urls):
                raise ValueError("production service URLs must be explicitly configured")

    @model_validator(mode="after")
    def validate_collection_settings(self) -> "Settings":
        if self.COLLECTION_TASK_DEADLINE_SECONDS <= self.COLLECTION_ADAPTER_TIMEOUT_SECONDS:
            raise ValueError("collection task deadline must exceed adapter timeout")
        if self.COLLECTION_STALE_RUN_AFTER_SECONDS <= self.COLLECTION_TASK_DEADLINE_SECONDS:
            raise ValueError("stale run threshold must exceed task deadline")
        if self.COLLECTION_RETRY_MAX_SECONDS < self.COLLECTION_RETRY_BASE_SECONDS:
            raise ValueError("maximum retry delay must not be below base delay")
        if self.COLLECTION_LOCK_TTL_SECONDS <= self.COLLECTION_ADAPTER_TIMEOUT_SECONDS:
            raise ValueError("collection lock TTL must exceed adapter timeout")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
