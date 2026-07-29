from celery import Celery

from market_intelligence.core.config import get_settings

settings = get_settings()
celery_app = Celery(
    "market_intelligence",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=["market_intelligence.tasks.health"],
)
celery_app.conf.update(
    enable_utc=True,
    timezone="UTC",
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    beat_schedule={},
)
