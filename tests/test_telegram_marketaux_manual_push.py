import importlib.util
import inspect
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from market_intelligence.feed.marketaux_feed import VisibleFeedItem
from market_intelligence.telegram.manual_push import (
    HttpxTelegramTransport,
    ManualTelegramPushService,
    TelegramRuntimeCredential,
)

_SCRIPT_SPEC = importlib.util.spec_from_file_location(
    "telegram_marketaux_push_smoke",
    Path(__file__).parents[1] / "scripts" / "telegram_marketaux_push_smoke.py",
)
assert _SCRIPT_SPEC is not None and _SCRIPT_SPEC.loader is not None
telegram_smoke = importlib.util.module_from_spec(_SCRIPT_SPEC)
_SCRIPT_SPEC.loader.exec_module(telegram_smoke)

TOKEN = "synthetic-telegram-token-never-print"
CHAT_ID = "synthetic-chat-id-never-print"


def _item() -> VisibleFeedItem:
    return VisibleFeedItem(
        content_item_id=uuid.uuid4(),
        title="Synthetic visible headline",
        source="Marketaux",
        provider="marketaux",
        published_at=datetime(2026, 8, 6, tzinfo=UTC),
        canonical_url="https://example.invalid/visible",
        provider_item_id="visible-1",
        collected_at=datetime(2026, 8, 6, 0, 1, tzinfo=UTC),
        raw_item_id=uuid.uuid4(),
        evidence_item_id=uuid.uuid4(),
    )


def test_preview_contains_only_required_visible_fields() -> None:
    message = ManualTelegramPushService().preview((_item(),))

    assert "Synthetic visible headline" in message
    assert "Source: Marketaux" in message
    assert "Time: 2026-08-06T00:00:00+00:00" in message
    assert "https://example.invalid/visible" in message
    assert all(term not in message.lower() for term in ("raw response", "body", "snippet"))


@pytest.mark.asyncio
async def test_preview_does_not_read_credentials(monkeypatch) -> None:
    async def recent(self, limit):
        del self, limit
        return (_item(),)

    class GuardedEnvironment(dict[str, str]):
        def get(self, key, default=None):
            raise AssertionError(f"credential read during preview: {key}")

    monkeypatch.setattr(telegram_smoke.MarketauxFeedService, "recent", recent)
    report, exit_code = await telegram_smoke.run_push(
        execute=False, limit=3, environ=GuardedEnvironment()
    )

    assert exit_code == 0
    assert report["status"] == "DRY_RUN"
    assert report["credential_read"] is False
    assert "Synthetic visible headline" in str(report["preview"])


@pytest.mark.asyncio
async def test_execute_missing_credentials_fails_after_safe_feed_read(monkeypatch) -> None:
    async def recent(self, limit):
        del self, limit
        return (_item(),)

    monkeypatch.setattr(telegram_smoke.MarketauxFeedService, "recent", recent)
    report, exit_code = await telegram_smoke.run_push(execute=True, limit=1, environ={})

    assert exit_code == 2
    assert report["credential_read"] is True
    assert report["safe_errors"] == ["telegram_credential_missing"]


@pytest.mark.asyncio
async def test_execute_empty_feed_does_not_read_credentials_or_send(monkeypatch) -> None:
    async def recent(self, limit):
        del self, limit
        return ()

    class GuardedEnvironment(dict[str, str]):
        def get(self, key, default=None):
            raise AssertionError(f"credential read for empty feed: {key}")

    class GuardedTransport:
        async def send(self, credential, message):
            del credential, message
            raise AssertionError("Telegram request sent for empty feed")

    monkeypatch.setattr(telegram_smoke.MarketauxFeedService, "recent", recent)
    report, exit_code = await telegram_smoke.run_push(
        execute=True,
        limit=1,
        environ=GuardedEnvironment(),
        transport=GuardedTransport(),
    )

    assert exit_code == 2
    assert report["mode"] == "execute"
    assert report["credential_read"] is False
    assert report["request_enabled"] is False
    assert report["safe_errors"] == ["telegram_feed_empty"]


@pytest.mark.asyncio
async def test_execute_uses_mocked_transport_and_redacts_credentials(monkeypatch) -> None:
    observed: dict[str, object] = {}

    async def recent(self, limit):
        del self
        assert limit == 1
        return (_item(),)

    def handler(request: httpx.Request) -> httpx.Response:
        observed["path"] = request.url.path
        payload = json.loads(request.content)
        observed["message"] = payload["text"]
        assert payload["chat_id"] == CHAT_ID
        return httpx.Response(200, json={"ok": True, "result": {"sensitive": "ignored"}})

    monkeypatch.setattr(telegram_smoke.MarketauxFeedService, "recent", recent)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        report, exit_code = await telegram_smoke.run_push(
            execute=True,
            limit=1,
            environ={"TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_CHAT_ID": CHAT_ID},
            transport=HttpxTelegramTransport(client),
        )

    assert exit_code == 0
    assert report["status"] == "PASS"
    assert report["sent"] is True
    assert "Synthetic visible headline" in str(observed["message"])
    rendered = repr(report) + repr(TelegramRuntimeCredential(TOKEN, CHAT_ID))
    assert TOKEN not in rendered
    assert CHAT_ID not in rendered
    assert "result" not in rendered


def test_invalid_limit_fails_without_runtime(capsys, monkeypatch) -> None:
    def fail(*args, **kwargs):
        del args, kwargs
        raise AssertionError("invalid limit opened runtime")

    monkeypatch.setattr(telegram_smoke, "create_engine", fail)
    exit_code = telegram_smoke.main(["--execute", "--limit", "6"])
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert report["safe_errors"] == ["telegram_limit_invalid"]
    assert report["credential_read"] is False


def test_manual_push_source_has_no_forbidden_dependencies() -> None:
    import market_intelligence.telegram.manual_push as module

    source = inspect.getsource(module).lower() + inspect.getsource(telegram_smoke).lower()
    forbidden = (
        "dotenv",
        "local_evaluation",
        "provider_capture",
        "scheduler",
        "openai",
        "recommendation",
        "dedup",
        "import event",
        "sqlalchemy.orm",
    )
    assert all(term not in source for term in forbidden)
