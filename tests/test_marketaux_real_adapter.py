import inspect
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest

from market_intelligence.providers import (
    HttpxProviderTransport,
    MarketauxRealAdapter,
    MockProviderTransport,
    ProviderAdapterErrorCode,
    ProviderFetchRequest,
    ProviderTransportResponse,
    ProviderTransportTimeout,
    RuntimeCredential,
)

SECRET = "synthetic-secret-never-print"


def _request(**overrides: object) -> ProviderFetchRequest:
    values: dict[str, object] = {
        "source_id": uuid4(),
        "source_account_id": None,
        "cursor": None,
        "config": {
            "query": "technology",
            "language": "en",
            "symbols": ("AAPL",),
            "timeout_seconds": 5,
        },
        "limit": 1,
        "deadline_at": datetime.now(UTC) + timedelta(minutes=1),
        "correlation_id": "synthetic-real-boundary",
    }
    values.update(overrides)
    return ProviderFetchRequest(**values)  # type: ignore[arg-type]


def _credential() -> RuntimeCredential:
    return RuntimeCredential("MARKETAUX_API_TOKEN", SECRET)


def _response(
    *, status: int = 200, body: object | None = None, headers: dict[str, str] | None = None
) -> ProviderTransportResponse:
    return ProviderTransportResponse(
        status_code=status,
        received_at=datetime(2026, 8, 4, tzinfo=UTC),
        body=(
            body
            if body is not None
            else {
                "data": [
                    {
                        "uuid": "synthetic-real-item",
                        "published_at": "2026-08-04T00:00:00Z",
                        "title": "value is never emitted",
                        "description": "value is never emitted",
                        "snippet": "value is never emitted",
                        "url": "https://example.invalid/not-emitted",
                    }
                ]
            }
        ),
        headers=headers or {},
    )


@pytest.mark.asyncio
async def test_real_adapter_builds_non_secret_provider_request() -> None:
    transport = MockProviderTransport([_response()])

    result = await MarketauxRealAdapter(_credential()).fetch(_request(), transport)

    request = transport.calls[0]
    assert request.provider == "marketaux"
    assert request.operation == "news_all"
    assert request.params == {
        "search": "technology",
        "limit": 1,
        "page": 1,
        "language": "en",
        "symbols": "AAPL",
    }
    assert request.runtime_credential is not None
    assert SECRET not in repr(request)
    assert SECRET not in repr(result)


@pytest.mark.asyncio
async def test_http_transport_injects_official_query_only_at_wire_boundary() -> None:
    observed = {"called": False}

    def handler(request: httpx.Request) -> httpx.Response:
        observed["called"] = True
        assert request.method == "GET"
        assert request.url.scheme == "https"
        assert request.url.host == "api.marketaux.com"
        assert request.url.path == "/v1/news/all"
        assert request.url.params["api_token"] == SECRET
        assert request.url.params["search"] == "technology"
        return httpx.Response(200, json={"data": []}, headers={"X-RateLimit-Remaining": "9"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter_transport = MockProviderTransport([_response()])
        await MarketauxRealAdapter(_credential()).fetch(_request(), adapter_transport)
        provider_request = adapter_transport.calls[0]
        response = await HttpxProviderTransport(client).send(provider_request)

    assert observed["called"] is True
    assert response.status_code == 200
    assert response.headers["x-ratelimit-remaining"] == "9"
    assert set(response.headers) <= {"content-type", "x-ratelimit-remaining"}
    assert SECRET not in repr(provider_request)
    assert SECRET not in repr(response)


@pytest.mark.asyncio
async def test_missing_credential_fails_closed_without_transport() -> None:
    transport = MockProviderTransport([_response()])
    result = await MarketauxRealAdapter(None).fetch(_request(), transport)

    assert result.safe_errors[0].code is ProviderAdapterErrorCode.CONFIG_INVALID
    assert result.safe_errors[0].safe_message == "provider_runtime_credential_missing"
    assert transport.calls == []


@pytest.mark.asyncio
async def test_success_returns_metadata_only_raw_item_and_sanitized_display_fields() -> None:
    result = await MarketauxRealAdapter(_credential()).fetch(
        _request(), MockProviderTransport([_response()])
    )

    assert result.safe_errors == ()
    assert len(result.raw_items) == 1
    assert result.raw_items[0].external_id == "synthetic-real-item"
    assert result.raw_items[0].retention_class == "metadata_only"
    assert result.sanitized_metadata[0]["has_title"] is True
    assert result.sanitized_metadata[0]["display_title"] == "value is never emitted"
    assert result.sanitized_metadata[0]["display_url"] == ("https://example.invalid/not-emitted")
    rendered = repr(result)
    assert SECRET not in rendered


@pytest.mark.asyncio
async def test_provider_echoed_secret_never_reaches_result() -> None:
    body = {
        "api_token": SECRET,
        "request": {"authorization": SECRET},
        "data": [
            {
                "uuid": "synthetic-real-item",
                "published_at": "2026-08-04T00:00:00Z",
                "title": f"token={SECRET}",
                "url": f"https://example.invalid/?api_token={SECRET}",
                "authorization": SECRET,
            }
        ],
    }
    result = await MarketauxRealAdapter(_credential()).fetch(
        _request(), MockProviderTransport([_response(body=body)])
    )

    assert result.safe_errors == ()
    assert SECRET not in repr(result)
    assert "authorization" not in result.sanitized_metadata[0]["field_names"]
    assert result.sanitized_metadata[0]["display_title"] is None
    assert result.sanitized_metadata[0]["display_url"] is None


@pytest.mark.asyncio
async def test_rate_limit_retry_after_is_safe() -> None:
    result = await MarketauxRealAdapter(_credential()).fetch(
        _request(),
        MockProviderTransport([_response(status=429, headers={"Retry-After": "12"})]),
    )
    error = result.safe_errors[0]
    assert error.code is ProviderAdapterErrorCode.RATE_LIMITED
    assert error.retry_after_seconds == 12
    assert SECRET not in repr(error)


@pytest.mark.asyncio
async def test_timeout_is_safe() -> None:
    result = await MarketauxRealAdapter(_credential()).fetch(
        _request(), MockProviderTransport([ProviderTransportTimeout()])
    )
    assert result.safe_errors[0].code is ProviderAdapterErrorCode.TIMEOUT
    assert SECRET not in repr(result)


@pytest.mark.asyncio
async def test_http_transport_timeout_does_not_echo_wire_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("synthetic timeout", request=request)

    provider_transport = MockProviderTransport([_response()])
    await MarketauxRealAdapter(_credential()).fetch(_request(), provider_transport)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderTransportTimeout) as caught:
            await HttpxProviderTransport(client).send(provider_transport.calls[0])
    assert str(caught.value) == "provider_request_timed_out"
    assert caught.value.__cause__ is None
    assert SECRET not in repr(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "retryable"), [(400, False), (401, False), (500, True), (503, True)]
)
async def test_http_errors_are_safely_classified(status: int, retryable: bool) -> None:
    result = await MarketauxRealAdapter(_credential()).fetch(
        _request(), MockProviderTransport([_response(status=status)])
    )
    assert result.safe_errors[0].code is ProviderAdapterErrorCode.UPSTREAM_ERROR
    assert result.safe_errors[0].retryable is retryable
    assert SECRET not in repr(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [None, {}, {"data": {}}, {"data": ["bad"]}])
async def test_malformed_response_fails_closed(body: object) -> None:
    response = ProviderTransportResponse(
        status_code=200,
        received_at=datetime(2026, 8, 4, tzinfo=UTC),
        body=body,
    )
    result = await MarketauxRealAdapter(_credential()).fetch(
        _request(), MockProviderTransport([response])
    )
    assert result.safe_errors[0].code is ProviderAdapterErrorCode.CONTRACT_INVALID


@pytest.mark.asyncio
async def test_record_limit_fails_before_transport() -> None:
    transport = MockProviderTransport([_response()])
    result = await MarketauxRealAdapter(_credential()).fetch(_request(limit=4), transport)
    assert result.safe_errors[0].code is ProviderAdapterErrorCode.CONFIG_INVALID
    assert transport.calls == []


@pytest.mark.asyncio
async def test_cursor_is_deterministic_and_secret_free() -> None:
    transport = MockProviderTransport([_response()])
    first = await MarketauxRealAdapter(_credential()).fetch(_request(), transport)
    second = await MarketauxRealAdapter(_credential()).fetch(
        _request(cursor=first.next_cursor), MockProviderTransport([_response()])
    )
    assert first.next_cursor == second.next_cursor
    assert SECRET not in (first.next_cursor or "")


def test_real_adapter_modules_do_not_read_local_state_or_import_forbidden_layers() -> None:
    from market_intelligence.providers import credentials, http_transport, marketaux_real

    source = "\n".join(
        inspect.getsource(module) for module in (credentials, http_transport, marketaux_real)
    ).lower()
    forbidden = (
        "getenv",
        "os.environ",
        "dotenv",
        "local_evaluation",
        "provider_capture",
        "scheduler",
        "evidencewriteservice",
        "evidence_items",
        "sqlalchemy",
        "openai",
        "telegram",
        "recommendation",
        "portfolio",
        "holding",
    )
    assert all(term not in source for term in forbidden)
