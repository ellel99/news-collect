#!/usr/bin/env python3
"""Manual Marketaux collection-to-evidence command; default is inert dry-run."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Mapping, Sequence

from redis.asyncio import Redis
from sqlalchemy import select

from market_intelligence.core.config import Settings
from market_intelligence.db.models import CollectionCursor
from market_intelligence.db.session import create_engine, create_session_factory
from market_intelligence.evidence.end_to_end import EndToEndStatus
from market_intelligence.pipeline.marketaux_real_collection import (
    MarketauxRealCollectionPipeline,
    resolve_marketaux_target,
)
from market_intelligence.providers.credentials import RuntimeCredential
from market_intelligence.providers.http_transport import HttpxProviderTransport

_PROVIDER = "marketaux"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one manual Marketaux collection pipeline")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--limit", type=int, default=1)
    return parser


def _summary(
    *,
    status: str,
    collection_status: str,
    raw_item_count: int = 0,
    evidence_item_count: int = 0,
    cursor_present: bool = False,
    safe_errors: list[str] | None = None,
    db_written: bool = False,
    token_read: bool = False,
) -> dict[str, object]:
    return {
        "provider": _PROVIDER,
        "status": status,
        "collection_status": collection_status,
        "raw_item_count": raw_item_count,
        "evidence_item_count": evidence_item_count,
        "cursor_present": cursor_present,
        "response_saved": False,
        "safe_errors": safe_errors or [],
        "db_written": db_written,
        "token_read": token_read,
    }


async def execute_collection(
    *, limit: int, environ: Mapping[str, str]
) -> tuple[dict[str, object], int]:
    token = environ.get("MARKETAUX_API_TOKEN", "")
    if not token:
        return (
            _summary(
                status="BLOCKED",
                collection_status="not_started",
                safe_errors=["provider_runtime_credential_missing"],
            ),
            2,
        )

    settings = Settings(  # type: ignore[call-arg]  # pydantic-settings init source control
        COLLECTION_BATCH_LIMIT=limit,
        _env_file=None,
    )
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        resolved = await resolve_marketaux_target(factory)
        if resolved is None:
            return (
                _summary(
                    status="BLOCKED",
                    collection_status="not_started",
                    safe_errors=["marketaux_target_not_unique"],
                    token_read=True,
                ),
                2,
            )
        pipeline = MarketauxRealCollectionPipeline(
            factory,
            redis,
            settings,
            RuntimeCredential("MARKETAUX_API_TOKEN", token),
            HttpxProviderTransport(),
        )
        outcome = await pipeline.run(resolved.target)
        evidence_count = sum(
            1
            for trigger in outcome.trigger_outcomes
            if trigger.pipeline_outcome is not None
            and trigger.pipeline_outcome.evidence_item_id is not None
        )
        errors = [error.code for error in outcome.safe_errors]
        for trigger in outcome.trigger_outcomes:
            errors.extend(error.code for error in trigger.safe_errors)
            if trigger.pipeline_outcome is not None:
                errors.extend(error.code for error in trigger.pipeline_outcome.safe_errors)
        async with factory() as session:
            cursor_present = (
                await session.scalar(
                    select(CollectionCursor.id).where(
                        CollectionCursor.source_account_id == resolved.target.source_account_id
                    )
                )
                is not None
            )
        succeeded = (
            outcome.status is EndToEndStatus.PROCESSED
            and not errors
            and outcome.raw_item_count > 0
            and evidence_count > 0
            and cursor_present
        )
        return (
            _summary(
                status="PASS" if succeeded else "FAIL",
                collection_status=outcome.status.value,
                raw_item_count=outcome.raw_item_count,
                evidence_item_count=evidence_count,
                cursor_present=cursor_present,
                safe_errors=errors,
                db_written=outcome.raw_item_count > 0 or evidence_count > 0,
                token_read=True,
            ),
            0 if succeeded else 3,
        )
    finally:
        await redis.aclose()
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not 1 <= args.limit <= 3:
        report = _summary(
            status="BLOCKED",
            collection_status="not_started",
            safe_errors=["provider_record_limit_invalid"],
        )
        exit_code = 2
    elif not args.execute:
        report = _summary(status="DRY_RUN", collection_status="not_started")
        exit_code = 0
    else:
        report, exit_code = asyncio.run(execute_collection(limit=args.limit, environ=os.environ))
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
