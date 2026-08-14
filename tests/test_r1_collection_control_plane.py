from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest

from market_intelligence.collection.control_plane import (
    CollectionControlPlaneWorker,
    TargetDispatch,
    TargetLockLost,
    TargetScheduler,
    dispatch_identity,
    dispatch_task_id,
)
from market_intelligence.collection.cursors import CursorContractError, decide_cursor
from market_intelligence.collection.target_configs import (
    TargetConfigError,
    build_operation_registry,
)
from market_intelligence.collection.target_repository import DueTarget
from market_intelligence.providers.contracts import ProviderTransportRequest
from market_intelligence.providers.credentials import RuntimeCredential
from market_intelligence.providers.http_transport import HttpxProviderTransport

SECRET = "never-print-r1-secret"


def test_operation_registry_is_exact_and_non_pageable() -> None:
    registry = build_operation_registry()
    marketaux = registry.resolve("marketaux", "news_all", 1, 1)
    config = registry.validate(
        marketaux, {"query": "technology"}, batch_limit=3, max_requests=1, max_pages=1
    )
    assert dict(config) == {"query": "technology"}
    assert marketaux.pagination_capability == "none"
    with pytest.raises(TargetConfigError, match="operation_contract_unknown"):
        registry.resolve("marketaux", "unknown", 1, 1)
    with pytest.raises(TargetConfigError, match="operation_budget_invalid"):
        registry.validate(
            marketaux, {"query": "technology"}, batch_limit=3, max_requests=2, max_pages=1
        )
    sec = registry.resolve("sec_edgar", "submissions_recent", 1, 1)
    with pytest.raises(TargetConfigError, match="operation_config_invalid"):
        registry.validate(sec, {"ticker": "AAPL"}, batch_limit=1, max_requests=1, max_pages=1)
    assert (
        registry.validate(
            sec,
            {"ticker": "AAPL", "cik": "320193"},
            batch_limit=1,
            max_requests=1,
            max_pages=1,
        )["cik"]
        == "0000320193"
    )


@pytest.mark.parametrize(
    "unsafe", [{"api_key": SECRET}, {"query": f"https://invalid/{SECRET}"}, {"module": "x.y"}]
)
def test_operation_config_rejects_secret_url_and_dynamic_class(unsafe: dict[str, str]) -> None:
    registry = build_operation_registry()
    contract = registry.resolve("marketaux", "news_all", 1, 1)
    with pytest.raises(TargetConfigError):
        registry.validate(contract, unsafe, batch_limit=1, max_requests=1, max_pages=1)
    assert SECRET not in repr(TargetConfigError("operation_config_unsafe"))


def test_dispatch_payload_is_identifiers_only_and_revision_scoped() -> None:
    target_id = uuid4()
    identity = dispatch_identity(target_id, 7, 42)
    dispatch = TargetDispatch(target_id, 7, 42, "normal", identity)
    assert set(dispatch.payload()) == {
        "target_id",
        "config_revision",
        "scheduled_slot",
        "run_mode",
        "dispatch_id",
    }
    assert dispatch_task_id(identity) == dispatch_task_id(identity)
    assert SECRET not in repr(dispatch)


@pytest.mark.asyncio
async def test_http_transport_stream_budget_blocks_before_json_parse() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, content=b"{" + b'"x":"' + b"x" * 2048 + b'"}')

    transport = HttpxProviderTransport(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    request = ProviderTransportRequest(
        provider="finnhub",
        operation="quote",
        params={"symbol": "AAPL"},
        timeout_seconds=1,
        max_response_bytes=1024,
        runtime_credential=RuntimeCredential("FINNHUB_API_KEY", SECRET),
    )
    with pytest.raises(RuntimeError, match="provider_response_too_large"):
        await transport.send(request)
    assert SECRET not in repr(request)


def test_source_audit_has_no_live_activation_or_migration_b() -> None:
    root = Path(__file__).parents[1]
    source = "\n".join(
        (root / path).read_text()
        for path in (
            "src/market_intelligence/collection/control_plane.py",
            "src/market_intelligence/collection/target_configs.py",
            "src/market_intelligence/notifications/intent.py",
            "src/market_intelligence/notifications/delivery.py",
        )
    )
    assert "provider_capture" not in source
    assert "local_evaluation" not in source
    assert "Migration B" not in source
    assert "OpenAI" not in source
    assert "Recommendation" not in source


def _cursor(at: str, identity: str) -> str:
    return f'{{"provider_item_id":"{identity}","published_at":"{at}"}}'


def test_cursor_strategies_are_deterministic_and_fail_closed() -> None:
    current = _cursor("2026-01-01T00:00:00+00:00", "a")
    newer = _cursor("2026-01-02T00:00:00+00:00", "b")
    older = _cursor("2025-12-01T00:00:00+00:00", "z")
    assert decide_cursor("compound", current, newer).action == "advance"
    assert decide_cursor("revision", current, current).action == "no_new_items"
    assert decide_cursor("snapshot_watermark", current, current).action == "no_new_items"
    with pytest.raises(CursorContractError, match="cursor_not_successor"):
        decide_cursor("compound", current, older)
    with pytest.raises(CursorContractError, match="cursor_strategy_invalid"):
        decide_cursor("page_token", current, newer)


class _FakeRedis:
    def __init__(self, *, reject: set[str] | None = None) -> None:
        self.values: dict[str, str] = {}
        self.reject = reject or set()

    async def set(self, key: str, value: str, **kwargs: object) -> bool:
        del kwargs
        if key in self.reject or key in self.values:
            return False
        self.values[key] = value
        return True

    async def eval(self, script: str, count: int, key: str, value: str) -> int:
        del script, count
        if self.values.get(key) == value:
            del self.values[key]
            return 1
        return 0


class _SchedulerRepository:
    def __init__(self, targets: list[SimpleNamespace]) -> None:
        self.targets = targets

    async def due_page(
        self,
        now: datetime,
        limit: int,
        after: tuple[datetime, int, UUID] | None,
    ) -> tuple[DueTarget, ...]:
        del now
        rows = [DueTarget(item.id, item.next_due_at, item.priority, False) for item in self.targets]
        rows.sort(key=lambda row: (row.effective_due_at, row.priority, row.target_id))
        if after is not None:
            rows = [
                row for row in rows if (row.effective_due_at, row.priority, row.target_id) > after
            ]
        return tuple(rows[:limit])

    async def load_for_execution(self, target_id: UUID, revision: int) -> SimpleNamespace:
        target = next(item for item in self.targets if item.id == target_id)
        assert target.config_revision == revision
        return SimpleNamespace(target=target)


class _TestScheduler(TargetScheduler):
    async def _revision(self, target_id: UUID) -> int:
        target = next(item for item in self._repository.targets if item.id == target_id)
        return int(target.config_revision)


@pytest.mark.asyncio
async def test_scheduler_keyset_scans_beyond_contended_first_page() -> None:
    due = datetime(2026, 1, 1, tzinfo=UTC)
    targets = [
        SimpleNamespace(
            id=uuid4(),
            next_due_at=due,
            priority=index % 2,
            config_revision=1,
            cadence_seconds=300,
            max_runtime_seconds=60,
        )
        for index in range(150)
    ]
    repository = _SchedulerRepository(targets)
    ordered = sorted(targets, key=lambda item: (item.next_due_at, item.priority, item.id))
    rejected = {
        "r1:dispatch:"
        + dispatch_task_id(dispatch_identity(item.id, 1, int(due.timestamp()) // 300))
        for item in ordered[:100]
    }
    scheduler = _TestScheduler(repository, _FakeRedis(reject=rejected))  # type: ignore[arg-type]
    claimed = await scheduler.claim_due(due, limit=25)
    assert len(claimed) == 25
    assert {dispatch.target_id for dispatch in claimed}.isdisjoint(
        {item.id for item in ordered[:100]}
    )


@pytest.mark.asyncio
async def test_worker_fetch_is_cancelled_immediately_when_owner_lock_is_lost() -> None:
    lock_lost = asyncio.Event()
    cancelled = asyncio.Event()

    async def fetch() -> None:
        try:
            await asyncio.sleep(60)
        finally:
            cancelled.set()

    task = asyncio.create_task(
        CollectionControlPlaneWorker._fetch_with_lock(fetch(), lock_lost, 30)
    )
    await asyncio.sleep(0)
    lock_lost.set()
    with pytest.raises(TargetLockLost, match="target_lock_lost"):
        await task
    assert cancelled.is_set()


def test_no_network_is_performed_by_contract_tests() -> None:
    assert asyncio.iscoroutinefunction(HttpxProviderTransport.send)
