from typing import Any

import pytest

from market_intelligence.tasks import collection, marketaux_telegram, multi_provider_scheduler
from market_intelligence.tasks.celery_app import (
    celery_app,
    legacy_schedule,
    shadow_schedule,
    unified_schedule,
)
from market_intelligence.tasks.health import health_ping


def test_health_ping_has_no_side_effects() -> None:
    assert marketaux_telegram.run_marketaux_telegram_cycle.name == "marketaux.telegram.run"
    assert multi_provider_scheduler.run_multi_provider_cycle.name == "multi_provider.telegram.run"
    assert health_ping.run() == {"status": "ok"}


def test_collection_tasks_and_beat_entries_are_registered() -> None:
    assert {
        "collection.dispatch_due_targets",
        "collection.run_target",
        "collection.recover_stale_runs",
        "marketaux.telegram.run",
        "multi_provider.telegram.run",
    } <= set(celery_app.tasks)
    assert {entry["task"] for entry in celery_app.conf.beat_schedule.values()} == {
        "collection.dispatch_due_targets",
        "collection.recover_stale_runs",
        "multi_provider.telegram.run",
    }


def test_authority_schedules_keep_shadow_read_only_and_default_legacy() -> None:
    legacy_tasks = {entry["task"] for entry in legacy_schedule.values()}
    shadow_tasks = {entry["task"] for entry in shadow_schedule.values()}
    unified_tasks = {entry["task"] for entry in unified_schedule.values()}
    assert shadow_tasks == legacy_tasks | {"collection.control_plane.shadow_audit"}
    assert "collection.control_plane.dispatch" not in shadow_tasks
    assert "multi_provider.telegram.run" not in unified_tasks
    assert celery_app.conf.beat_schedule == legacy_schedule


def test_run_target_eager_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run(
        target_data: dict[str, Any], collection_run_id: str | None, attempt: int
    ) -> tuple[dict[str, Any], float | None]:
        del target_data, collection_run_id, attempt
        return {"collection_run_id": "synthetic", "status": "succeeded"}, None

    monkeypatch.setattr(collection, "_run", fake_run)
    assert collection.run_target.run({"source_id": "unused"}) == {
        "collection_run_id": "synthetic",
        "status": "succeeded",
    }
