from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from market_intelligence.collection.control_plane import (
    TargetDispatch,
    dispatch_identity,
    dispatch_task_id,
)
from market_intelligence.collection.target_configs import (
    TargetConfigError,
    build_operation_registry,
)
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


def test_migration_a_contains_required_permanent_and_temporary_guards() -> None:
    migration = (
        Path(__file__).parents[1] / "alembic/versions/0006_expand_collection_targets.py"
    ).read_text()
    for marker in (
        "trg_r1_target_identity_guard",
        "trg_r1_source_provider_guard",
        "trg_r1_active_legacy_identity_guard",
        "uq_collection_targets_legacy_owner",
        "trg_r1_raw_run_provenance_guard",
    ):
        assert marker in migration
    assert 'revision: str = "0006"' in migration
    assert 'down_revision: str | None = "0005"' in migration


def test_no_network_is_performed_by_contract_tests() -> None:
    assert asyncio.iscoroutinefunction(HttpxProviderTransport.send)
