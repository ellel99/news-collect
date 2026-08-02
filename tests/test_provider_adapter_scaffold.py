from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from market_intelligence.providers import (
    MarketauxAdapter,
    MockProviderTransport,
    ProviderAdapterErrorCode,
    ProviderAdapterNotRegistered,
    ProviderAdapterRegistry,
    ProviderFetchRequest,
    ProviderTransportRequest,
    ProviderTransportResponse,
    ProviderTransportTimeout,
)

SECRET = "do-not-expose-this-value"


def _request(**overrides: object) -> ProviderFetchRequest:
    values: dict[str, object] = {
        "source_id": uuid4(),
        "source_account_id": None,
        "cursor": None,
        "config": {"query": "technology", "timeout_seconds": 5},
        "limit": 1,
        "deadline_at": datetime.now(UTC) + timedelta(minutes=1),
        "correlation_id": "synthetic-correlation",
    }
    values.update(overrides)
    return ProviderFetchRequest(**values)  # type: ignore[arg-type]


def _response(
    *,
    status: int = 200,
    body: object | None = None,
    headers: dict[str, str] | None = None,
) -> ProviderTransportResponse:
    return ProviderTransportResponse(
        status_code=status,
        received_at=datetime(2026, 1, 1, tzinfo=UTC),
        body=(
            body
            if body is not None
            else {
                "data": [
                    {
                        "uuid": "synthetic-item-1",
                        "published_at": "2026-01-01T00:00:00Z",
                        "title": "synthetic title",
                        "description": "synthetic description",
                        "snippet": "synthetic snippet",
                        "url": "https://example.invalid/synthetic",
                    }
                ]
            }
        ),
        headers=headers or {},
    )


@pytest.mark.asyncio
async def test_registry_registers_and_resolves_marketaux_adapter() -> None:
    registry = ProviderAdapterRegistry()
    adapter = MarketauxAdapter()
    registry.register("marketaux", adapter)
    assert registry.get("marketaux") is adapter
    assert registry.supports("marketaux") is True


def test_registry_unknown_provider_fails_closed_without_fallback() -> None:
    registry = ProviderAdapterRegistry()
    with pytest.raises(ProviderAdapterNotRegistered) as exc_info:
        registry.get(f"unknown-{SECRET}")
    assert str(exc_info.value) == "provider_adapter_unregistered"
    assert SECRET not in str(exc_info.value)


@pytest.mark.asyncio
async def test_marketaux_mock_response_builds_safe_raw_item_envelope() -> None:
    transport = MockProviderTransport([_response()])
    result = await MarketauxAdapter().fetch(_request(), transport)
    assert result.provider == "marketaux"
    assert result.contract_version == 1
    assert result.safe_errors == ()
    assert len(result.raw_items) == 1
    item = result.raw_items[0]
    assert item.external_id == "synthetic-item-1"
    assert item.http_status == 200
    assert item.retention_class == "metadata_only"
    assert item.payload_location is not None
    assert item.payload_location.startswith("internal://provider/marketaux/")
    assert item.payload_hash is not None and len(item.payload_hash) == 64
    assert result.sanitized_metadata[0]["has_title"] is True
    assert "synthetic title" not in repr(result)
    assert "https://example.invalid" not in repr(result)


@pytest.mark.asyncio
async def test_provider_echoed_secret_is_removed_from_output() -> None:
    body = {
        "api_token": SECRET,
        "request": {"authorization": SECRET},
        "data": [
            {
                "uuid": "synthetic-item-1",
                "published_at": "2026-01-01T00:00:00Z",
                "title": f"api_key={SECRET}",
                "url": f"https://example.invalid/?token={SECRET}",
                "api_key": SECRET,
                "authorization": SECRET,
            }
        ],
    }
    result = await MarketauxAdapter().fetch(
        _request(), MockProviderTransport([_response(body=body)])
    )
    serialized = repr(result)
    assert result.safe_errors == ()
    assert SECRET not in serialized
    assert "api_key" not in result.sanitized_metadata[0]["field_names"]
    assert "authorization" not in result.sanitized_metadata[0]["field_names"]
    assert result.sanitized_metadata[0]["has_title"] is True
    assert result.sanitized_metadata[0]["has_source_url"] is True


@pytest.mark.asyncio
async def test_record_limit_caps_output_and_marks_has_more() -> None:
    data = [
        {
            "uuid": f"synthetic-{index}",
            "published_at": f"2026-01-01T00:00:0{index}Z",
        }
        for index in range(3)
    ]
    result = await MarketauxAdapter().fetch(
        _request(limit=2), MockProviderTransport([_response(body={"data": data})])
    )
    assert len(result.raw_items) == 2
    assert result.has_more is True


@pytest.mark.asyncio
async def test_limit_above_scaffold_max_fails_before_transport() -> None:
    transport = MockProviderTransport([_response()])
    result = await MarketauxAdapter().fetch(_request(limit=4), transport)
    assert result.raw_items == ()
    assert result.safe_errors[0].code is ProviderAdapterErrorCode.CONFIG_INVALID
    assert transport.calls == []


@pytest.mark.asyncio
async def test_cursor_is_deterministic_and_used_as_safe_watermark() -> None:
    data = [
        {"uuid": "b", "published_at": "2026-01-01T00:00:00Z"},
        {"uuid": "a", "published_at": "2026-01-02T00:00:00Z"},
    ]
    first_transport = MockProviderTransport([_response(body={"data": data})])
    first = await MarketauxAdapter().fetch(_request(limit=2), first_transport)
    assert first.next_cursor == ('{"provider_item_id":"a","published_at":"2026-01-02T00:00:00Z"}')

    second_transport = MockProviderTransport([_response(body={"data": data})])
    second = await MarketauxAdapter().fetch(
        _request(limit=2, cursor=first.next_cursor), second_transport
    )
    assert second.next_cursor == first.next_cursor
    assert second_transport.calls[0].params["published_after"] == "2026-01-02T00:00:00Z"


@pytest.mark.asyncio
async def test_invalid_cursor_fails_closed_before_transport() -> None:
    transport = MockProviderTransport([_response()])
    result = await MarketauxAdapter().fetch(_request(cursor="not-json"), transport)
    assert result.safe_errors[0].safe_message == "provider_cursor_invalid"
    assert transport.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [None, [], {}, {"data": {}}, {"data": ["invalid"]}])
async def test_malformed_response_fails_closed(body: object) -> None:
    response = ProviderTransportResponse(
        status_code=200,
        received_at=datetime(2026, 1, 1, tzinfo=UTC),
        body=body,
    )
    result = await MarketauxAdapter().fetch(_request(), MockProviderTransport([response]))
    assert result.raw_items == ()
    assert result.safe_errors[0].code is ProviderAdapterErrorCode.CONTRACT_INVALID


@pytest.mark.asyncio
async def test_rate_limit_maps_to_safe_retryable_error() -> None:
    result = await MarketauxAdapter().fetch(
        _request(),
        MockProviderTransport([_response(status=429, headers={"Retry-After": "15"})]),
    )
    error = result.safe_errors[0]
    assert error.code is ProviderAdapterErrorCode.RATE_LIMITED
    assert error.retryable is True
    assert error.retry_after_seconds == 15


@pytest.mark.asyncio
async def test_timeout_maps_to_safe_retryable_error() -> None:
    result = await MarketauxAdapter().fetch(
        _request(), MockProviderTransport([ProviderTransportTimeout()])
    )
    assert result.safe_errors[0].code is ProviderAdapterErrorCode.TIMEOUT
    assert result.safe_errors[0].retryable is True


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 500, 503])
async def test_provider_error_maps_to_safe_error(status: int) -> None:
    result = await MarketauxAdapter().fetch(
        _request(), MockProviderTransport([_response(status=status)])
    )
    assert result.raw_items == ()
    assert result.safe_errors[0].code is ProviderAdapterErrorCode.UPSTREAM_ERROR
    assert result.safe_errors[0].retryable is (status >= 500)


@pytest.mark.asyncio
async def test_unsafe_config_fails_before_transport_without_echo() -> None:
    transport = MockProviderTransport([_response()])
    result = await MarketauxAdapter().fetch(_request(config={"api_token": SECRET}), transport)
    assert result.safe_errors[0].safe_message == "provider_config_fields_invalid"
    assert SECRET not in repr(result)
    assert transport.calls == []


def test_transport_request_rejects_secret_fields_and_values() -> None:
    with pytest.raises(ValueError, match="provider_transport_request_contains_secret"):
        ProviderTransportRequest(
            provider="marketaux",
            operation="news_all",
            params={"api_token": SECRET},
            timeout_seconds=5,
        )
    with pytest.raises(ValueError, match="provider_transport_request_contains_secret"):
        ProviderTransportRequest(
            provider="marketaux",
            operation="news_all",
            params={"search": f"api_key={SECRET}"},
            timeout_seconds=5,
        )


def test_adapter_scaffold_has_no_forbidden_runtime_dependencies() -> None:
    root = Path(__file__).parents[1] / "src/market_intelligence/providers"
    sources = "\n".join(
        (root / name).read_text(encoding="utf-8")
        for name in ("contracts.py", "registry.py", "transport.py", "marketaux.py")
    )
    for forbidden in (
        "import requests",
        "import httpx",
        "urlopen",
        "socket",
        "EvidenceWriteService",
        "evidence_items",
        "AsyncSession",
        "sqlalchemy",
        "provider_capture",
        "local_evaluation",
        "CollectionRunner",
        "scheduler",
        "OpenAI",
        "Telegram",
        "Recommendation",
        "Portfolio",
        "Holding",
    ):
        assert forbidden not in sources


def test_scaffold_files_do_not_mutate_database_or_read_local_files() -> None:
    root = Path(__file__).parents[1] / "src/market_intelligence/providers"
    sources = "\n".join(
        (root / name).read_text(encoding="utf-8")
        for name in ("contracts.py", "registry.py", "transport.py", "marketaux.py")
    )
    forbidden_operations = (
        "session.add",
        ".execute(",
        ".commit(",
        "Path(",
        "open(",
        "getenv",
        "environ",
    )
    for forbidden in forbidden_operations:
        assert forbidden not in sources
