"""Unified, bounded runtime verification orchestration for three providers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from market_intelligence.pipeline.provider_runtime import (
    dry_run_summary,
    execute_provider,
    inspect_provider_target,
)

PROVIDER_ORDER = ("finnhub", "eia", "sec_edgar")
PROVIDER_OPTIONS: dict[str, Mapping[str, object]] = {
    "finnhub": {"symbol": "AAPL"},
    "eia": {"dataset": "electricity"},
    "sec_edgar": {"ticker": "AAPL", "cik": "0000320193"},
}


class VerificationMode(StrEnum):
    DRY_RUN = "dry_run"
    DOCTOR = "doctor"
    BOOTSTRAP = "bootstrap"
    EXECUTE = "execute"


@dataclass(frozen=True, slots=True)
class MultiProviderVerificationReport:
    status: str
    reports: tuple[Mapping[str, object], ...]

    def safe_dict(self) -> dict[str, object]:
        return {"status": self.status, "reports": [dict(report) for report in self.reports]}


Executor = Callable[
    [str, int, Mapping[str, str], Mapping[str, object]],
    Awaitable[tuple[dict[str, object], int]],
]
Inspector = Callable[..., Awaitable[tuple[dict[str, object], int]]]


async def run_multi_provider_verification(
    mode: VerificationMode,
    environ: Mapping[str, str],
    *,
    executor: Executor = execute_provider,
    inspector: Inspector = inspect_provider_target,
) -> tuple[MultiProviderVerificationReport, int]:
    """Run each provider once, serially, with content-free summaries."""

    reports: list[Mapping[str, object]] = []
    codes: list[int] = []
    if mode is VerificationMode.DRY_RUN:
        reports = [dry_run_summary(provider, 1) for provider in PROVIDER_ORDER]
        return MultiProviderVerificationReport("DRY_RUN", tuple(reports)), 0
    if mode in (VerificationMode.DOCTOR, VerificationMode.BOOTSTRAP):
        for provider in PROVIDER_ORDER:
            report, code = await inspector(provider, bootstrap=mode is VerificationMode.BOOTSTRAP)
            reports.append(report)
            codes.append(code)
    else:
        for provider in PROVIDER_ORDER:
            report, code = await executor(provider, 1, environ, PROVIDER_OPTIONS[provider])
            reports.append(report)
            codes.append(code)
    passed = all(code == 0 for code in codes)
    return MultiProviderVerificationReport("PASS" if passed else "BLOCKED", tuple(reports)), (
        0 if passed else 2
    )
