#!/usr/bin/env python3
"""Manual Marketaux Telegram preview/push; default never reads credentials or sends."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Mapping, Sequence

from market_intelligence.core.config import Settings
from market_intelligence.db.session import create_engine, create_session_factory
from market_intelligence.feed.marketaux_feed import MarketauxFeedService
from market_intelligence.telegram.manual_push import (
    HttpxTelegramTransport,
    ManualTelegramPushService,
    TelegramRuntimeCredential,
    TelegramTransport,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Preview or manually push Marketaux feed")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--limit", type=int, default=3)
    return parser


async def run_push(
    *,
    execute: bool,
    limit: int,
    environ: Mapping[str, str],
    transport: TelegramTransport | None = None,
) -> tuple[dict[str, object], int]:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    engine = create_engine(settings)
    try:
        items = await MarketauxFeedService(create_session_factory(engine)).recent(limit)
    except Exception:
        return _report("BLOCKED", 0, execute, False, False, None, ["feed_read_failed"]), 2
    finally:
        await engine.dispose()
    if not items:
        return _report("BLOCKED", 0, execute, False, False, None, ["telegram_feed_empty"]), 2

    service = ManualTelegramPushService()
    try:
        preview = service.preview(items)
    except ValueError as exc:
        return _report("BLOCKED", len(items), execute, False, False, None, [str(exc)]), 2
    if not execute:
        return _report("DRY_RUN", len(items), False, False, False, preview, []), 0

    token = environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return (
            _report(
                "BLOCKED",
                len(items),
                True,
                True,
                False,
                None,
                ["telegram_credential_missing"],
            ),
            2,
        )

    try:
        result = await service.push(
            items,
            TelegramRuntimeCredential(token, chat_id),
            transport or HttpxTelegramTransport(),
        )
    except RuntimeError:
        return (
            _report("FAIL", len(items), True, True, False, None, ["telegram_transport_failed"]),
            3,
        )
    passed = 200 <= result.status_code < 300
    return (
        _report(
            "PASS" if passed else "FAIL",
            len(items),
            True,
            True,
            passed,
            None,
            [] if passed else ["telegram_request_rejected"],
        ),
        0 if passed else 3,
    )


def _report(
    status: str,
    item_count: int,
    execute_requested: bool,
    credential_read: bool,
    sent: bool,
    preview: str | None,
    errors: list[str],
) -> dict[str, object]:
    return {
        "provider": "marketaux",
        "status": status,
        "mode": "execute" if execute_requested else "dry_run",
        "item_count": item_count,
        "preview": preview,
        "request_enabled": execute_requested and credential_read,
        "sent": sent,
        "credential_read": credential_read,
        "response_saved": False,
        "safe_errors": errors,
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not 1 <= args.limit <= 5:
        report, exit_code = (
            _report("BLOCKED", 0, args.execute, False, False, None, ["telegram_limit_invalid"]),
            2,
        )
    else:
        report, exit_code = asyncio.run(
            run_push(execute=args.execute, limit=args.limit, environ=os.environ)
        )
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
