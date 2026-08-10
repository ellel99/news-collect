from celery import Celery

from market_intelligence.core.config import Settings

settings = Settings(_env_file=None)  # type: ignore[call-arg]
celery_app = Celery(
    "market_intelligence",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=[
        "market_intelligence.tasks.health",
        "market_intelligence.tasks.collection",
        "market_intelligence.tasks.marketaux_telegram",
    ],
)
celery_app.conf.update(
    enable_utc=True,
    timezone="UTC",
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    beat_schedule={
        "collection-dispatch": {
            "task": "collection.dispatch_due_targets",
            "schedule": settings.COLLECTION_DISPATCH_INTERVAL_SECONDS,
        },
        "collection-stale-run-recovery": {
            "task": "collection.recover_stale_runs",
            "schedule": settings.COLLECTION_STALE_RUN_SCAN_SECONDS,
        },
        "marketaux-telegram-cycle": {
            "task": "marketaux.telegram.run",
            "schedule": settings.MARKETAUX_TELEGRAM_SCHEDULER_INTERVAL_SECONDS,
        },
    },
)
