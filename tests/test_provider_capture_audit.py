from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType


def _load_script(name: str) -> ModuleType:
    path = Path(__file__).parents[1] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


provider_capture_audit = _load_script("provider_capture_audit")


def _capture() -> dict[str, object]:
    return {
        "capture_version": 1,
        "provider": "marketaux",
        "captured_at": "2026-08-01T00:00:00+00:00",
        "endpoint_family": "https://api.marketaux.com/v1/news/all",
        "request": {
            "method": "GET",
            "non_secret_params": {"limit": 1, "search": "artificial intelligence"},
            "secret_param_names": ["api_token"],
            "secret_header_names": [],
            "context": {"query": "artificial intelligence"},
            "limit": 1,
        },
        "http_status": 200,
        "safe_response_headers": {"content-type": "application/json"},
        "response_body": {
            "meta": {"returned": 1},
            "data": [
                {
                    "uuid": "mock-id",
                    "title": "SECRET RAW TITLE",
                    "url": "https://example.invalid/secret-url",
                    "description": "SECRET RAW BODY",
                    "published_at": "2026-08-01T00:00:00Z",
                }
            ],
        },
    }


def test_audit_report_contains_hash_size_and_no_raw_content(tmp_path: Path) -> None:
    path = tmp_path / "capture.json"
    path.write_text(json.dumps(_capture()), encoding="utf-8")
    report = provider_capture_audit.audit_capture(path)
    serialized = json.dumps(report)
    assert report["file_size_bytes"] == path.stat().st_size
    assert len(str(report["sha256"])) == 64
    assert report["result_count"] == 1
    assert "SECRET RAW TITLE" not in serialized
    assert "SECRET RAW BODY" not in serialized
    assert "secret-url" not in serialized
    assert report["replay_ready"] is True


def test_audit_detects_secret_and_request_url_risks(tmp_path: Path) -> None:
    capture = _capture()
    capture["api_token"] = "do-not-leak"
    capture["request_url"] = "https://example.invalid?api_token=do-not-leak"
    capture["authorization"] = "Bearer do-not-leak"
    path = tmp_path / "capture.json"
    path.write_text(json.dumps(capture), encoding="utf-8")
    report = provider_capture_audit.audit_capture(path)
    serialized = json.dumps(report)
    assert report["has_secret_detected"] is True
    assert report["has_raw_request_url_with_secret"] is True
    assert report["has_authorization_header"] is True
    assert report["replay_ready"] is False
    assert "do-not-leak" not in serialized


def test_audit_marks_out_of_limit_capture_not_replay_ready(tmp_path: Path) -> None:
    capture = _capture()
    request = capture["request"]
    assert isinstance(request, dict)
    request["limit"] = 4
    path = tmp_path / "capture.json"
    path.write_text(json.dumps(capture), encoding="utf-8")
    report = provider_capture_audit.audit_capture(path)
    assert report["within_limit"] is False
    assert report["replay_ready"] is False
