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
        "market_intelligence.tasks.multi_provider_scheduler",
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
        "multi-provider-telegram-cycle": {
            "task": "multi_provider.telegram.run",
            "schedule": settings.MULTI_PROVIDER_SCHEDULER_TICK_SECONDS,
        },
    },
)
