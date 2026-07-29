import socket
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from random import Random
from uuid import uuid4

import pytest

from market_intelligence.collection.contracts import (
    CollectionTarget,
    CursorSnapshot,
    FetchRequest,
)
from market_intelligence.collection.errors import ClassifiedCollectionError, CollectionErrorCode
from market_intelligence.collection.fake import FakeCollectionAdapter
from market_intelligence.collection.registry import AdapterRegistry, build_fake_registry
from market_intelligence.collection.retry import RetryPolicy
from market_intelligence.collection.scheduler import (
    dispatch_key,
    scheduled_slot,
    source_is_due,
    task_id_for,
)
from market_intelligence.db.models import Source


def target(options: dict[str, object] | None = None) -> CollectionTarget:
    return CollectionTarget(uuid4(), uuid4(), "rss", "fake", "metadata_only", options or {})


def test_contracts_are_immutable_and_options_are_copied() -> None:
    options: dict[str, object] = {"behavior": "empty"}
    value = target(options)
    options["behavior"] = "error"
    assert value.collection_options["behavior"] == "empty"
    with pytest.raises(FrozenInstanceError):
        value.access_method = "web"  # type: ignore[misc]
    with pytest.raises(TypeError):
        value.collection_options["secret"] = "value"  # type: ignore[index]


@pytest.mark.asyncio
async def test_fake_adapter_is_deterministic_and_network_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def deny_network(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("fake adapter attempted external network access")

    monkeypatch.setattr(socket, "socket", deny_network)
    adapter = FakeCollectionAdapter()
    request = FetchRequest(
        target({"behavior": "items", "pages": 1}),
        CursorSnapshot(adapter.cursor_type, None, None),
        100,
        datetime.now(UTC),
    )
    batch = await adapter.fetch(request)
    assert batch.items[0].external_id == "fake-1"
    assert batch.next_cursor == "1"
    assert not batch.has_more


def test_registry_only_allows_fake_and_unknown_fails_closed() -> None:
    registry = build_fake_registry()
    assert isinstance(registry.resolve("fake"), FakeCollectionAdapter)
    with pytest.raises(ClassifiedCollectionError) as caught:
        registry.resolve("web")
    assert caught.value.code == CollectionErrorCode.CONFIG_INVALID
    with pytest.raises(ValueError, match="only the fake"):
        AdapterRegistry().register("rss", FakeCollectionAdapter())


@pytest.mark.parametrize(
    ("code", "retryable"),
    [
        (
            code,
            code.value
            in {
                "COLLECTION_TIMEOUT",
                "COLLECTION_NETWORK",
                "COLLECTION_RATE_LIMITED",
                "COLLECTION_UPSTREAM_5XX",
                "COLLECTION_UPSTREAM_RETRYABLE",
                "COLLECTION_DATABASE_UNAVAILABLE",
                "COLLECTION_LOCK_LOST",
            },
        )
        for code in CollectionErrorCode
    ],
)
def test_all_error_codes_have_expected_retry_class(
    code: CollectionErrorCode, retryable: bool
) -> None:
    assert ClassifiedCollectionError(code, "detail").retryable is retryable


def test_retry_policy_uses_full_jitter_and_retry_after_cap() -> None:
    policy = RetryPolicy(3, 5, 300, 900)
    error = ClassifiedCollectionError(CollectionErrorCode.NETWORK, "network")
    assert 0 <= policy.delay(error, 2, Random(7)) <= 20
    limited = ClassifiedCollectionError(
        CollectionErrorCode.RATE_LIMITED, "limited", retry_after=1200
    )
    assert policy.delay(limited, 0, Random(7)) == 900
    assert policy.should_retry(error, 2)
    assert not policy.should_retry(error, 3)


def test_dispatch_identity_is_deterministic() -> None:
    value = target()
    now = datetime(2026, 7, 29, tzinfo=UTC)
    key = dispatch_key(value, scheduled_slot(now, 30))
    assert task_id_for(key) == task_id_for(key)


def test_due_time_applies_failure_backoff() -> None:
    now = datetime(2026, 7, 29, 12, tzinfo=UTC)
    source = Source(
        schedule_seconds=60,
        consecutive_failures=2,
    )
    assert not source_is_due(source, now, now)
    assert source_is_due(source, now, now.replace(minute=4))
