from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import httpx
import pytest

from market_intelligence.providers.preflight import eia, marketaux

SECRET = "capture-secret-must-not-leak"


def _load_script(name: str) -> ModuleType:
    path = Path(__file__).parents[1] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


provider_capture = _load_script("provider_capture")
provider_capture_audit = _load_script("provider_capture_audit")
provider_replay = _load_script("provider_replay")


def _args(provider: str, *, limit: int = 1) -> argparse.Namespace:
    return argparse.Namespace(
        provider=provider,
        query="artificial intelligence",
        symbol="AAPL",
        dataset="electricity",
        ticker="AAPL",
        limit=limit,
        env_file=None,
        execute=False,
    )


def test_dry_run_does_not_access_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(provider_capture, "load_environment", lambda path: {})

    def unexpected_capture(*args: object, **kwargs: object) -> object:
        raise AssertionError("dry-run must not access network")

    monkeypatch.setattr(provider_capture, "create_capture", unexpected_capture)
    assert provider_capture.main(["--provider", "marketaux", "--limit", "1"]) == 0


def test_cli_rejects_key_and_token_arguments() -> None:
    with pytest.raises(SystemExit):
        provider_capture.main(["--provider", "marketaux", "--api-key", SECRET])
    with pytest.raises(SystemExit):
        provider_capture.main(["--provider", "marketaux", "--token", SECRET])


@pytest.mark.parametrize(
    ("provider", "limit"),
    [
        ("marketaux", 0),
        ("marketaux", 4),
        ("eia", 0),
        ("eia", 6),
        ("sec_edgar", 0),
        ("sec_edgar", 11),
    ],
)
def test_provider_limits_fail_closed(provider: str, limit: int) -> None:
    with pytest.raises(ValueError):
        provider_capture._validate_args(_args(provider, limit=limit))


def test_eia_dataset_fails_closed() -> None:
    args = _args("eia")
    args.dataset = "petroleum"
    with pytest.raises(ValueError):
        provider_capture._validate_args(args)


def test_finnhub_empty_symbol_fails_closed() -> None:
    args = _args("finnhub")
    args.symbol = ""
    with pytest.raises(ValueError):
        provider_capture._validate_args(args)


def test_sec_ticker_scaffold_is_bounded_to_three() -> None:
    args = _args("sec_edgar")
    args.ticker = "TSLA"
    with pytest.raises(ValueError):
        provider_capture._validate_args(args)
    assert len(provider_capture.SEC_CIK_BY_TICKER) == 3


def test_execute_with_mock_transport_saves_raw_structure_without_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture_root = tmp_path / "local_evaluation" / "raw_provider_captures"
    monkeypatch.setattr(provider_capture, "CAPTURE_ROOT", capture_root)
    request = marketaux.build_request(
        {"MARKETAUX_API_TOKEN": SECRET},
        query="artificial intelligence",
        limit=1,
        execute=True,
    )
    payload = {
        "meta": {"returned": 1},
        "data": [
            {
                "uuid": "mock-id",
                "title": "mock raw title",
                "url": "https://example.invalid/raw",
                "published_at": "2026-08-01T00:00:00Z",
            }
        ],
    }
    transport = httpx.MockTransport(lambda incoming: httpx.Response(200, json=payload))
    report = provider_capture.create_capture(
        request,
        context={"query": "artificial intelligence"},
        limit=1,
        transport=transport,
        captured_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    capture_file = tmp_path / str(report["capture_file"])
    loaded = json.loads(capture_file.read_text(encoding="utf-8"))
    assert loaded["response_body"] == payload
    serialized = json.dumps(loaded)
    assert SECRET not in serialized
    assert "api_token=" not in serialized
    assert "request_url" not in loaded
    assert loaded["request"]["secret_param_names"] == ["api_token"]


def test_sec_recent_columns_are_truncated_to_ten() -> None:
    payload = {
        "filings": {
            "recent": {
                "accessionNumber": [f"mock-{index}" for index in range(20)],
                "form": ["10-K"] * 20,
            }
        }
    }
    sanitized = provider_capture.sanitize_response_body(
        payload,
        provider="sec_edgar",
        limit=10,
    )
    recent = sanitized["filings"]["recent"]
    assert len(recent["accessionNumber"]) == 10
    assert len(recent["form"]) == 10


def test_eia_echoed_secrets_are_removed_before_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture_root = tmp_path / "local_evaluation" / "raw_provider_captures"
    monkeypatch.setattr(provider_capture, "CAPTURE_ROOT", capture_root)
    request = eia.build_request(
        {"EIA_API_KEY": SECRET, "EIA_API_VERSION": "v2"},
        dataset="electricity",
        limit=1,
        execute=True,
    )
    row = {
        "period": "2026-01",
        "price": "mock-value",
        "sectorid": "RES",
        "stateid": "US",
    }
    payload = {
        "request": {
            "api_key": SECRET,
            "url": f"https://api.eia.gov/v2/example?api_key={SECRET}",
            "params": {"frequency": "monthly"},
        },
        "response": {"data": [row]},
    }
    transport = httpx.MockTransport(lambda incoming: httpx.Response(200, json=payload))
    report = provider_capture.create_capture(
        request,
        context={"dataset": "electricity"},
        limit=1,
        transport=transport,
        captured_at=datetime(2026, 8, 2, tzinfo=UTC),
    )
    capture_file = tmp_path / str(report["capture_file"])
    loaded = json.loads(capture_file.read_text(encoding="utf-8"))
    serialized_capture = json.dumps(loaded)
    assert loaded["response_body"]["request"] == {"params": {"frequency": "monthly"}}
    assert loaded["response_body"]["response"]["data"] == [row]
    assert SECRET not in serialized_capture
    assert "api_key=" not in serialized_capture

    audit = provider_capture_audit.audit_capture(capture_file)
    serialized_audit = json.dumps(audit)
    assert audit["has_secret_detected"] is False
    assert audit["has_raw_request_url_with_secret"] is False
    assert audit["replay_ready"] is True
    assert SECRET not in serialized_audit
    assert "mock-value" not in serialized_audit

    replay = provider_replay.replay_summary(loaded)
    serialized_replay = json.dumps(replay)
    assert replay["replay_ready"] is True
    assert replay["input_items"] == 1
    assert SECRET not in serialized_replay
    assert "mock-value" not in serialized_replay


def test_capture_fails_closed_when_sanitizer_leaves_secret_risk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture_root = tmp_path / "local_evaluation" / "raw_provider_captures"
    monkeypatch.setattr(provider_capture, "CAPTURE_ROOT", capture_root)
    monkeypatch.setattr(
        provider_capture,
        "sanitize_response_body",
        lambda payload, *, provider, limit: {"api_key": SECRET},
    )
    request = eia.build_request(
        {"EIA_API_KEY": SECRET, "EIA_API_VERSION": "v2"},
        dataset="electricity",
        limit=1,
        execute=True,
    )
    transport = httpx.MockTransport(lambda incoming: httpx.Response(200, json={"response": {}}))
    with pytest.raises(ValueError, match="could not be sanitized"):
        provider_capture.create_capture(
            request,
            context={"dataset": "electricity"},
            limit=1,
            transport=transport,
            captured_at=datetime(2026, 8, 2, tzinfo=UTC),
        )
    assert not capture_root.exists()


def test_local_evaluation_is_ignored_and_not_tracked() -> None:
    root = Path(__file__).parents[1]
    gitignore = (root / ".gitignore").read_text(encoding="utf-8")
    assert "local_evaluation/" in gitignore
    ignored = subprocess.run(
        ["git", "check-ignore", "local_evaluation/raw_provider_captures/mock.json"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert ignored.returncode == 0
    tracked = subprocess.run(
        ["git", "ls-files", "local_evaluation"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    assert tracked.stdout == ""
    package_review = (root / "scripts/package-review.sh").read_text(encoding="utf-8")
    assert "--exclude 'local_evaluation/'" in package_review
