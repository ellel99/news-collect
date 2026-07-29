from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from market_intelligence.collection.contracts import FetchBatch, FetchRequest, RawItemEnvelope
from market_intelligence.collection.errors import ClassifiedCollectionError, CollectionErrorCode


class FakeCollectionAdapter:
    """Deterministic, network-free adapter used only by tests."""

    cursor_type: str | None = "fake_sequence"

    async def fetch(self, request: FetchRequest) -> FetchBatch:
        behavior = str(request.target.collection_options.get("behavior", "empty"))
        if behavior == "timeout":
            await asyncio.sleep(3600)
        if behavior == "error":
            raise ClassifiedCollectionError(
                CollectionErrorCode.UPSTREAM_RETRYABLE, "synthetic fake adapter failure"
            )
        if behavior == "empty":
            return FetchBatch()

        sequence = int(request.cursor.cursor_value or "0") + 1
        maximum = int(request.target.collection_options.get("pages", 1))
        now = datetime.now(UTC)
        item = RawItemEnvelope(
            external_id=f"fake-{sequence}",
            fetched_at=now,
            http_status=200,
            content_type="application/x.fake",
            payload_location=None,
            payload_hash=f"{sequence:064x}",
            retention_class=request.target.retention_class,
        )
        return FetchBatch(
            items=(item,),
            next_cursor=str(sequence),
            last_published_at=now,
            has_more=sequence < maximum,
        )

    def is_cursor_successor(self, current: str | None, candidate: str) -> bool:
        try:
            return int(candidate) == int(current or "0") + 1
        except ValueError:
            return False
