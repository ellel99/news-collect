import inspect
import json
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from market_intelligence.pipeline.multi_provider_verification import (
    PROVIDER_ORDER,
    VerificationMode,
    run_multi_provider_verification,
)
from market_intelligence.providers.contracts import ProviderFetchRequest, ProviderTransportResponse
from market_intelligence.providers.credentials import RuntimeCredential
from market_intelligence.providers.eia import EiaAdapter
from market_intelligence.providers.sec_edgar import SecEdgarAdapter
from market_intelligence.providers.transport import MockProviderTransport


class GuardedEnvironment(dict[str, str]):
    def get(self, key: str, default: str | None = None) -> str | None:
        raise AssertionError(f"credential read during dry-run: {key}")


@pytest.mark.asyncio
async def test_default_dry_run_is_inert() -> None:
    async def forbidden_executor(*args, **kwargs):
        raise AssertionError("executor called during dry-run")

    async def forbidden_inspector(*args, **kwargs):
        raise AssertionError("DB inspector called during dry-run")

    report, code = await run_multi_provider_verification(
        VerificationMode.DRY_RUN,
        GuardedEnvironment(),
        executor=forbidden_executor,
        inspector=forbidden_inspector,
    )

    assert code == 0
    assert report.status == "DRY_RUN"
    assert tuple(item["provider"] for item in report.reports) == PROVIDER_ORDER
    assert all(item["credential_read"] is False for item in report.reports)
    assert all(item["request_enabled"] is False for item in report.reports)
    assert all(item["db_written"] is False for item in report.reports)


@pytest.mark.asyncio
async def test_execute_calls_each_provider_once_serially() -> None:
    calls: list[str] = []

    async def executor(provider, limit, environ, options):
        assert limit == 1
        assert options
        calls.append(provider)
        return (
            {
                "provider": provider,
                "status": "PASS",
                "request_enabled": True,
                "credential_read": True,
                "raw_item_count": 1,
                "evidence_item_count": 1,
                "content_item_count": 1 if provider == "sec_edgar" else 0,
                "db_written": True,
                "response_saved": False,
                "has_more": True,
                "safe_errors": [],
            },
            0,
        )

    report, code = await run_multi_provider_verification(
        VerificationMode.EXECUTE, {}, executor=executor
    )

    assert code == 0
    assert report.status == "PASS"
    assert calls == list(PROVIDER_ORDER)
    assert len(calls) == len(set(calls)) == 3
    assert all(item["has_more"] is True for item in report.reports)


@pytest.mark.parametrize("mode", [VerificationMode.DOCTOR, VerificationMode.BOOTSTRAP])
@pytest.mark.asyncio
async def test_doctor_and_bootstrap_visit_each_provider_without_executor(mode) -> None:
    calls: list[tuple[str, bool]] = []

    async def inspector(provider, *, bootstrap):
        calls.append((provider, bootstrap))
        return ({"provider": provider, "status": "PASS", "safe_errors": []}, 0)

    async def forbidden_executor(*args, **kwargs):
        raise AssertionError("provider executor called")

    report, code = await run_multi_provider_verification(
        mode, GuardedEnvironment(), executor=forbidden_executor, inspector=inspector
    )

    assert code == 0
    assert report.status == "PASS"
    assert calls == [(provider, mode is VerificationMode.BOOTSTRAP) for provider in PROVIDER_ORDER]


def test_unified_cli_default_dry_run_is_safe() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/multi_provider_runtime_smoke.py"],
        check=False,
        capture_output=True,
        text=True,
        env={"PATH": os.environ.get("PATH", "")},
    )
    report = json.loads(completed.stdout)
    assert completed.returncode == 0
    assert report["status"] == "DRY_RUN"
    assert len(report["reports"]) == 3
    rendered = completed.stdout.lower()
    forbidden = ("api_key", "token=", "authorization", "raw_response", "filing_body")
    assert all(value not in rendered for value in forbidden)


@pytest.mark.asyncio
async def test_eia_and_sec_adapters_preserve_has_more_semantics() -> None:
    requests = {
        "eia": ProviderFetchRequest(
            uuid.uuid4(),
            uuid.uuid4(),
            None,
            {"dataset": "electricity"},
            1,
            datetime.now(UTC) + timedelta(seconds=10),
            "bounded",
        ),
        "sec_edgar": ProviderFetchRequest(
            uuid.uuid4(),
            uuid.uuid4(),
            None,
            {"ticker": "AAPL", "cik": "0000320193"},
            1,
            datetime.now(UTC) + timedelta(seconds=10),
            "bounded",
        ),
    }
    eia_body = {
        "response": {
            "data": [
                {"period": "2026-07", "price": 1, "stateid": "US", "sectorid": "ALL"},
                {"period": "2026-06", "price": 2, "stateid": "US", "sectorid": "ALL"},
            ]
        }
    }
    sec_body = {
        "filings": {
            "recent": {
                "accessionNumber": ["one", "two"],
                "filingDate": ["2026-08-01", "2026-07-01"],
                "form": ["10-Q", "8-K"],
                "primaryDocument": ["one.htm", "two.htm"],
            }
        }
    }
    adapters = {
        "eia": EiaAdapter(RuntimeCredential("EIA_API_KEY", "synthetic")),
        "sec_edgar": SecEdgarAdapter(RuntimeCredential("SEC_USER_AGENT", "synthetic contact")),
    }
    bodies = {"eia": eia_body, "sec_edgar": sec_body}
    for provider in ("eia", "sec_edgar"):
        result = await adapters[provider].fetch(
            requests[provider],
            MockProviderTransport(
                [ProviderTransportResponse(200, datetime.now(UTC), bodies[provider])]
            ),
        )
        assert len(result.raw_items) == 1
        assert result.has_more is True


def test_runtime_verification_source_has_no_forbidden_scope() -> None:
    import market_intelligence.pipeline.multi_provider_verification as module

    source = inspect.getsource(module).lower()
    forbidden = (
        "scheduler",
        "telegram",
        "openai",
        "recommendation",
        "formal_dedup",
        "clustering",
        "local_evaluation",
        "provider_capture",
        "dotenv",
    )
    assert all(value not in source for value in forbidden)
