import importlib.util
import inspect
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from market_intelligence.providers.contracts import ProviderTransportResponse
from market_intelligence.providers.transport import MockProviderTransport

_SCRIPT_SPEC = importlib.util.spec_from_file_location(
    "marketaux_live_smoke",
    Path(__file__).parents[1] / "scripts" / "marketaux_live_smoke.py",
)
assert _SCRIPT_SPEC is not None and _SCRIPT_SPEC.loader is not None
marketaux_live_smoke = importlib.util.module_from_spec(_SCRIPT_SPEC)
_SCRIPT_SPEC.loader.exec_module(marketaux_live_smoke)

SECRET = "live-smoke-secret-must-not-print"


def _response() -> ProviderTransportResponse:
    return ProviderTransportResponse(
        status_code=200,
        received_at=datetime(2026, 8, 4, tzinfo=UTC),
        body={
            "data": [
                {
                    "uuid": "synthetic-live-smoke-item",
                    "published_at": "2026-08-04T00:00:00Z",
                    "title": "real title must not print",
                    "description": "real body must not print",
                    "snippet": "real snippet must not print",
                    "url": "https://example.invalid/must-not-print",
                }
            ]
        },
    )


def _printed_report(capsys: pytest.CaptureFixture[str]) -> dict[str, object]:
    return json.loads(capsys.readouterr().out)


def test_default_dry_run_does_not_read_secret_or_call_transport(capsys) -> None:
    transport = MockProviderTransport([_response()])
    environ = {"MARKETAUX_API_TOKEN": SECRET}

    exit_code = marketaux_live_smoke.main([], environ=environ, transport=transport)
    report = _printed_report(capsys)

    assert exit_code == 0
    assert report["status"] == "DRY_RUN"
    assert report["credential_read"] is False
    assert report["request_enabled"] is False
    assert transport.calls == []
    assert SECRET not in repr(report)


def test_execute_without_token_fails_closed(capsys) -> None:
    transport = MockProviderTransport([_response()])
    exit_code = marketaux_live_smoke.main(["--execute"], environ={}, transport=transport)
    report = _printed_report(capsys)

    assert exit_code == 2
    assert report["status"] == "BLOCKED"
    assert report["safe_errors"] == ["provider_runtime_credential_missing"]
    assert transport.calls == []


def test_limit_above_three_fails_before_credential_or_transport(capsys) -> None:
    transport = MockProviderTransport([_response()])
    exit_code = marketaux_live_smoke.main(
        ["--execute", "--limit", "4"],
        environ={"MARKETAUX_API_TOKEN": SECRET},
        transport=transport,
    )
    report = _printed_report(capsys)

    assert exit_code == 2
    assert report["safe_errors"] == ["provider_record_limit_invalid"]
    assert transport.calls == []
    assert SECRET not in repr(report)


def test_mocked_execute_outputs_only_safe_summary(capsys) -> None:
    transport = MockProviderTransport([_response()])
    exit_code = marketaux_live_smoke.main(
        ["--execute", "--limit", "1"],
        environ={"MARKETAUX_API_TOKEN": SECRET},
        transport=transport,
    )
    report = _printed_report(capsys)

    assert exit_code == 0
    assert report["status"] == "PASS"
    assert report["item_count"] == 1
    assert len(transport.calls) == 1
    rendered = repr(report).lower()
    assert all(
        forbidden not in rendered
        for forbidden in (
            SECRET,
            "real title",
            "real body",
            "real snippet",
            "example.invalid",
            "raw response",
            "authorization",
            "title",
            "body",
            "url",
            "snippet",
            "description",
        )
    )


def test_script_has_no_env_file_database_or_pipeline_dependencies() -> None:
    source = inspect.getsource(marketaux_live_smoke).lower()
    forbidden = (
        "dotenv",
        "env_file",
        "path(",
        "open(",
        "local_evaluation",
        "provider_capture",
        "sqlalchemy",
        "evidence_items",
        "collectionrunner",
        "scheduler",
        "dedup",
        "openai",
        "telegram",
        "recommendation",
    )
    assert all(term not in source for term in forbidden)
