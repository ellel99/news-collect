from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

MARKETAUX_TITLE = "PRIVATE MARKET TITLE"
MARKETAUX_URL = "https://example.invalid/private-article"
MARKETAUX_BODY = "PRIVATE MARKET DESCRIPTION"
QUOTE_VALUE = 98765.4321
EIA_VALUE = "PRIVATE EIA VALUE"
SEC_ACCESSION = "PRIVATE-ACCESSION"
SEC_DOCUMENT = "private-document.htm"
SECRET = "private-api-secret"


def _load_script() -> ModuleType:
    path = Path(__file__).parents[1] / "scripts/provider_normalize_replay.py"
    spec = importlib.util.spec_from_file_location("provider_normalize_replay", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


provider_normalize_replay = _load_script()


def _capture(provider: str, *, count: int = 1) -> dict[str, object]:
    request: dict[str, object] = {"context": {}, "limit": count}
    if provider == "marketaux":
        request["context"] = {"query": "technology"}
        body: object = {
            "data": [
                {
                    "uuid": f"mock-{index}",
                    "title": MARKETAUX_TITLE,
                    "url": MARKETAUX_URL,
                    "snippet": "PRIVATE MARKET SNIPPET",
                    "description": MARKETAUX_BODY,
                    "published_at": "2026-08-02T00:00:00Z",
                    "source": "mock-source",
                    "entities": [{"symbol": "MOCK"}],
                    "keywords": ["mock"],
                    "language": "en",
                }
                for index in range(count)
            ]
        }
    elif provider == "finnhub":
        request["context"] = {"symbol": "MOCK"}
        body = {
            "c": QUOTE_VALUE,
            "d": 1.0,
            "dp": 0.1,
            "h": 2.0,
            "l": 0.5,
            "o": 1.5,
            "pc": 1.2,
            "t": 1,
        }
    elif provider == "eia":
        request["context"] = {"dataset": "electricity"}
        body = {
            "response": {
                "data": [
                    {
                        "period": f"2026-{index + 1:02d}",
                        "price": EIA_VALUE,
                        "sectorid": "RES",
                        "stateid": "US",
                        "sectorName": "mock-sector",
                        "stateDescription": "mock-state",
                        "price-units": "mock-unit",
                    }
                    for index in range(count)
                ]
            }
        }
    else:
        request["context"] = {"ticker": "MOCK"}
        body = {
            "filings": {
                "recent": {
                    "accessionNumber": [f"{SEC_ACCESSION}-{index}" for index in range(count)],
                    "filingDate": ["2026-08-02"] * count,
                    "acceptanceDateTime": ["2026-08-02T00:00:00Z"] * count,
                    "reportDate": ["2026-06-30"] * count,
                    "form": ["10-Q"] * count,
                    "primaryDocument": [SEC_DOCUMENT] * count,
                }
            }
        }
    return {
        "capture_version": 1,
        "provider": provider,
        "captured_at": "2026-08-02T00:00:00Z",
        "request": request,
        "response_body": body,
    }


def _assert_content_free(report: dict[str, object]) -> None:
    serialized = json.dumps(report)
    for forbidden in (
        MARKETAUX_TITLE,
        MARKETAUX_URL,
        MARKETAUX_BODY,
        "PRIVATE MARKET SNIPPET",
        str(QUOTE_VALUE),
        EIA_VALUE,
        SEC_ACCESSION,
        SEC_DOCUMENT,
        SECRET,
    ):
        assert forbidden not in serialized
    assert report["content_values_emitted"] is False


@pytest.mark.parametrize(
    ("provider", "expected_type"),
    [
        ("marketaux", "marketaux_news"),
        ("finnhub", "finnhub_quote"),
        ("eia", "eia_energy_timeseries"),
        ("sec_edgar", "sec_filing"),
    ],
)
def test_provider_candidate_is_content_free(provider: str, expected_type: str) -> None:
    report = provider_normalize_replay.normalize_capture(_capture(provider), "a" * 64)
    _assert_content_free(report)
    assert report["input_items"] == 1
    assert report["candidate_items"] == 1
    candidates = report["common_envelope_candidates"]
    assert isinstance(candidates, list)
    assert candidates[0]["provider_item_type"] == expected_type
    assert len(str(candidates[0]["provider_item_hash"])) == 64
    assert report["errors"] == []


def test_provider_specific_summaries_preserve_structural_differences() -> None:
    marketaux = provider_normalize_replay.normalize_capture(_capture("marketaux"), "a" * 64)
    finnhub = provider_normalize_replay.normalize_capture(_capture("finnhub"), "b" * 64)
    eia = provider_normalize_replay.normalize_capture(_capture("eia"), "c" * 64)
    sec = provider_normalize_replay.normalize_capture(_capture("sec_edgar"), "d" * 64)
    assert marketaux["provider_summary"]["title_available_count"] == 1
    assert finnhub["provider_summary"]["market_data_flag"] is True
    assert finnhub["provider_summary"]["numeric_value_field_count"] == 7
    assert eia["provider_summary"]["energy_evidence_flag"] is True
    assert sec["provider_summary"]["disclosure_flag"] is True
    for report in (marketaux, finnhub, eia, sec):
        _assert_content_free(report)


def test_directory_mode_aggregates_four_providers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture_root = tmp_path / "local_evaluation/raw_provider_captures"
    capture_root.mkdir(parents=True)
    monkeypatch.setattr(provider_normalize_replay, "CAPTURE_ROOT", capture_root)
    counts = {"marketaux": 3, "finnhub": 1, "eia": 5, "sec_edgar": 10}
    for provider, count in counts.items():
        (capture_root / f"{provider}.json").write_text(
            json.dumps(_capture(provider, count=count)),
            encoding="utf-8",
        )
    report = provider_normalize_replay.normalize_directory(capture_root)
    _assert_content_free(report)
    assert report["capture_files_seen"] == 4
    assert report["total_input_items"] == 19
    assert report["total_candidate_items"] == 19
    assert report["providers_seen"] == ["marketaux", "finnhub", "eia", "sec_edgar"]
    assert report["provider_type_counts"] == {
        "eia_energy_timeseries": 5,
        "finnhub_quote": 1,
        "marketaux_news": 3,
        "sec_filing": 10,
    }
    summaries = report["provider_summaries"]
    assert summaries["marketaux"]["title_available_count"] == 3
    assert summaries["finnhub"]["market_data_flag"] is True
    assert summaries["eia"]["official_source_flag"] is True
    assert summaries["sec_edgar"]["disclosure_flag"] is True
    assert report["errors"] == []


def test_unknown_provider_and_empty_items_fail_closed() -> None:
    with pytest.raises(provider_normalize_replay.ReplayCandidateError, match="unknown_provider"):
        provider_normalize_replay.normalize_capture(_capture("unknown"), "a" * 64)
    empty = _capture("marketaux")
    empty["response_body"] = {"data": []}
    with pytest.raises(provider_normalize_replay.ReplayCandidateError, match="no_input_items"):
        provider_normalize_replay.normalize_capture(empty, "a" * 64)


def test_secret_risk_fails_closed_without_emitting_secret() -> None:
    capture = _capture("eia")
    capture["response_body"] = {"request": {"api_key": SECRET}, "response": {"data": []}}
    with pytest.raises(provider_normalize_replay.ReplayCandidateError) as exc_info:
        provider_normalize_replay.normalize_capture(capture, "a" * 64)
    assert str(exc_info.value) == "secret_risk_detected"
    assert SECRET not in str(exc_info.value)


def test_invalid_json_and_outside_path_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture_root = tmp_path / "local_evaluation/raw_provider_captures"
    capture_root.mkdir(parents=True)
    monkeypatch.setattr(provider_normalize_replay, "CAPTURE_ROOT", capture_root)
    invalid = capture_root / "invalid.json"
    invalid.write_text("not-json", encoding="utf-8")
    with pytest.raises(provider_normalize_replay.ReplayCandidateError, match="invalid_capture"):
        provider_normalize_replay.normalize_file(invalid)
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps(_capture("marketaux")), encoding="utf-8")
    with pytest.raises(
        provider_normalize_replay.ReplayCandidateError,
        match="capture_path_outside_local_evaluation",
    ):
        provider_normalize_replay.normalize_file(outside)


def test_directory_missing_provider_and_empty_directory_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture_root = tmp_path / "local_evaluation/raw_provider_captures"
    capture_root.mkdir(parents=True)
    monkeypatch.setattr(provider_normalize_replay, "CAPTURE_ROOT", capture_root)
    with pytest.raises(provider_normalize_replay.ReplayCandidateError, match="no_captures_found"):
        provider_normalize_replay.normalize_directory(capture_root)
    (capture_root / "marketaux.json").write_text(
        json.dumps(_capture("marketaux")), encoding="utf-8"
    )
    with pytest.raises(
        provider_normalize_replay.ReplayCandidateError,
        match="missing_required_providers",
    ):
        provider_normalize_replay.normalize_directory(capture_root)


def test_script_has_no_network_database_or_ai_dependencies() -> None:
    source = (Path(__file__).parents[1] / "scripts/provider_normalize_replay.py").read_text(
        encoding="utf-8"
    )
    for forbidden in (
        "import httpx",
        "import requests",
        "urlopen",
        "sqlalchemy",
        "AsyncSession",
        "openai",
    ):
        assert forbidden not in source
