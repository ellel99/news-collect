from typing import Any

from market_intelligence.tasks.celery_app import celery_app


@celery_app.task(name="system.health_ping")  # type: ignore[untyped-decorator]
def health_ping() -> dict[str, Any]:
    return {"status": "ok"}
