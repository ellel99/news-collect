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


provider_replay = _load_script("provider_replay")


def test_replay_summary_contains_no_raw_content() -> None:
    capture = {
        "provider": "marketaux",
        "request": {"context": {"query": "technology"}},
        "response_body": {
            "data": [
                {
                    "uuid": "mock-id",
                    "title": "REAL RAW TITLE",
                    "url": "https://example.invalid/raw-url",
                    "description": "REAL RAW BODY",
                    "published_at": "2026-08-01T00:00:00Z",
                    "entities": [],
                }
            ]
        },
    }
    summary = provider_replay.replay_summary(capture)
    serialized = json.dumps(summary)
    assert summary["input_items"] == 1
    assert summary["normalized_items"] == 0
    assert summary["dedup_key_available_count"] == 1
    assert "REAL RAW TITLE" not in serialized
    assert "REAL RAW BODY" not in serialized
    assert "raw-url" not in serialized


def test_replay_does_not_import_network_client() -> None:
    source = (Path(__file__).parents[1] / "scripts" / "provider_replay.py").read_text(
        encoding="utf-8"
    )
    assert "import httpx" not in source
    assert "requests" not in source
    assert "urlopen" not in source


def test_sec_replay_uses_columnar_rows_without_values_in_summary() -> None:
    capture = {
        "provider": "sec_edgar",
        "request": {"context": {"ticker": "AAPL"}},
        "response_body": {
            "filings": {
                "recent": {
                    "accessionNumber": ["secret-accession"],
                    "filingDate": ["2026-08-01"],
                    "form": ["10-K"],
                    "primaryDocument": ["secret-document.htm"],
                }
            }
        },
    }
    summary = provider_replay.replay_summary(capture)
    serialized = json.dumps(summary)
    assert summary["input_items"] == 1
    assert summary["dedup_key_available_count"] == 1
    assert "secret-accession" not in serialized
    assert "secret-document" not in serialized
