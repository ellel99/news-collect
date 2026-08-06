"""Explicit manual Telegram preview and push with no scheduling behavior."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import httpx

from market_intelligence.feed.marketaux_feed import VisibleFeedItem


@dataclass(frozen=True, slots=True)
class TelegramRuntimeCredential:
    token: str = field(repr=False)
    chat_id: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class TelegramSendResult:
    status_code: int


class TelegramTransport(Protocol):
    async def send(
        self, credential: TelegramRuntimeCredential, message: str
    ) -> TelegramSendResult: ...


class HttpxTelegramTransport:
    """Minimal Bot API transport; response bodies are never retained."""

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client

    async def send(self, credential: TelegramRuntimeCredential, message: str) -> TelegramSendResult:
        path = f"https://api.telegram.org/bot{credential.token}/sendMessage"
        payload = {
            "chat_id": credential.chat_id,
            "text": message,
            "disable_web_page_preview": True,
        }
        try:
            if self._client is not None:
                response = await self._client.post(path, json=payload)
            else:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    response = await client.post(path, json=payload)
        except httpx.HTTPError:
            raise RuntimeError("telegram_transport_failed") from None
        return TelegramSendResult(response.status_code)


class ManualTelegramPushService:
    """Build a bounded plain-text preview and explicitly send it when invoked."""

    def preview(self, items: tuple[VisibleFeedItem, ...]) -> str:
        if not items:
            raise ValueError("telegram_feed_empty")
        blocks = [
            "\n".join(
                (
                    item.title,
                    f"Source: {item.source}",
                    f"Time: {item.published_at.isoformat()}",
                    item.canonical_url,
                )
            )
            for item in items
        ]
        message = "\n\n".join(blocks)
        if len(message) > 4096:
            raise ValueError("telegram_message_too_long")
        return message

    async def push(
        self,
        items: tuple[VisibleFeedItem, ...],
        credential: TelegramRuntimeCredential,
        transport: TelegramTransport,
    ) -> TelegramSendResult:
        return await transport.send(credential, self.preview(items))
