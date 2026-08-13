#!/usr/bin/env python3
"""Bounded Event impact smoke: dry-run by default, one candidate/request on execute."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from uuid import UUID

from sqlalchemy import func, select

from market_intelligence.core.config import Settings
from market_intelligence.db.models import EventCandidate, EventCandidateEvidence
from market_intelligence.db.session import create_engine, create_session_factory
from market_intelligence.event_intelligence.analyzers import (
    OpenAIResponsesImpactAnalyzer,
    OpenAIResponsesTransport,
)
from market_intelligence.event_intelligence.fact_layer import FactLayerBuilder
from market_intelligence.event_intelligence.persistence import AnalyzerIdentity
from market_intelligence.event_intelligence.runtime import EventProcessingRuntime
from market_intelligence.providers.credentials import RuntimeCredential


@dataclass(frozen=True, slots=True)
class SmokeSummary:
    status: str
    event_candidate_present: bool
    fact_built: bool
    credential_read: bool
    request_enabled: bool
    analysis_valid: bool
    analysis_written: bool
    analysis_version: int | None
    response_saved: bool = False
    safe_errors: tuple[str, ...] = ()

    def safe_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["safe_errors"] = list(self.safe_errors)
        return value


def dry_run() -> SmokeSummary:
    return SmokeSummary("DRY_RUN", False, False, False, False, False, False, None)


async def doctor() -> SmokeSummary:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            candidate_id = await _target(session)
            if candidate_id is None:
                return SmokeSummary(
                    "BLOCKED",
                    False,
                    False,
                    False,
                    False,
                    False,
                    False,
                    None,
                    safe_errors=("event_candidate_target_not_unique",),
                )
            try:
                await FactLayerBuilder().build(session, candidate_id)
            except ValueError as error:
                return SmokeSummary(
                    "BLOCKED",
                    True,
                    False,
                    False,
                    False,
                    False,
                    False,
                    None,
                    safe_errors=(str(error),),
                )
            return SmokeSummary("PASS", True, True, False, False, False, False, None)
    finally:
        await engine.dispose()


async def execute(environ: Mapping[str, str]) -> SmokeSummary:
    token = environ.get("OPENAI_API_KEY", "")
    model = environ.get("OPENAI_IMPACT_MODEL", "")
    if not token or not model:
        return SmokeSummary(
            "BLOCKED",
            False,
            False,
            True,
            False,
            False,
            False,
            None,
            safe_errors=("analyzer_runtime_config_missing",),
        )
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            candidate_id = await _target(session)
            if candidate_id is None:
                return SmokeSummary(
                    "BLOCKED",
                    False,
                    False,
                    True,
                    False,
                    False,
                    False,
                    None,
                    safe_errors=("event_candidate_target_not_unique",),
                )
            evidence_id = await session.scalar(
                select(EventCandidateEvidence.evidence_item_id)
                .where(
                    EventCandidateEvidence.event_candidate_id == candidate_id,
                    EventCandidateEvidence.active.is_(True),
                )
                .order_by(EventCandidateEvidence.added_at)
                .limit(1)
            )
            if evidence_id is None:
                return SmokeSummary(
                    "BLOCKED",
                    True,
                    False,
                    True,
                    False,
                    False,
                    False,
                    None,
                    safe_errors=("event_fact_evidence_insufficient",),
                )
            analyzer = OpenAIResponsesImpactAnalyzer(
                RuntimeCredential("OPENAI_API_KEY", token),
                model,
                OpenAIResponsesTransport(),
            )
            outcome = await EventProcessingRuntime().process_evidence(
                session,
                evidence_id,
                analyzer=analyzer,
                analyzer_identity=AnalyzerIdentity("openai_responses", model),
            )
            await session.commit()
            return SmokeSummary(
                outcome.status.value,
                outcome.event_candidate_id is not None,
                outcome.fact_snapshot_hash is not None,
                True,
                True,
                outcome.analysis_id is not None and not outcome.safe_errors,
                outcome.analysis_id is not None and outcome.status.value in {"PASS", "NO_CHANGE"},
                outcome.analysis_version,
                safe_errors=outcome.safe_errors,
            )
    finally:
        await engine.dispose()


async def _target(session: object) -> UUID | None:
    from sqlalchemy.ext.asyncio import AsyncSession

    assert isinstance(session, AsyncSession)
    candidates = (
        await session.scalars(
            select(EventCandidate.id)
            .join(EventCandidateEvidence)
            .where(EventCandidateEvidence.active.is_(True))
            .group_by(EventCandidate.id)
            .having(func.count(EventCandidateEvidence.id) > 0)
            .order_by(EventCandidate.latest_seen_at.desc())
            .limit(2)
        )
    ).all()
    return candidates[0] if len(candidates) == 1 else None


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--doctor", action="store_true")
    mode.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.execute:
        summary = asyncio.run(execute(os.environ))
    elif args.doctor:
        summary = asyncio.run(doctor())
    else:
        summary = dry_run()
    print(json.dumps(summary.safe_dict(), sort_keys=True, separators=(",", ":")))
    return 0 if summary.status in {"DRY_RUN", "PASS", "NO_CHANGE"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
