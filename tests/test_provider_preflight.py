from __future__ import annotations

import importlib.util
import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from market_intelligence.providers.preflight import eia, finnhub, marketaux, newsapi_ai, sec_edgar
from market_intelligence.providers.preflight.base import (
    MissingCredentialError,
    SmokeReport,
)

_SCRIPT_SPEC = importlib.util.spec_from_file_location(
    "provider_smoke",
    Path(__file__).parents[1] / "scripts" / "provider_smoke.py",
)
assert _SCRIPT_SPEC is not None and _SCRIPT_SPEC.loader is not None
provider_smoke = importlib.util.module_from_spec(_SCRIPT_SPEC)
_SCRIPT_SPEC.loader.exec_module(provider_smoke)

SECRET = "do-not-print-this-secret"


def _response(payload: object, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(200, json=payload, headers=headers)


@pytest.mark.parametrize(
    ("provider", "builder"),
    [
        (
            "newsapi_ai",
            lambda env: newsapi_ai.build_request(
                env, query="technology", max_results=1, execute=True
            ),
        ),
        (
            "marketaux",
            lambda env: marketaux.build_request(env, query="technology", limit=1, execute=True),
        ),
        (
            "finnhub",
            lambda env: finnhub.build_request(env, symbol="AAPL", execute=True),
        ),
        (
            "eia",
            lambda env: eia.build_request(
                {**env, "EIA_API_VERSION": "v2"},
                dataset="electricity",
                limit=1,
                execute=True,
            ),
        ),
        (
            "sec_edgar",
            lambda env: sec_edgar.build_request(env, ticker="AAPL", execute=True),
        ),
    ],
)
def test_execute_missing_credentials_fails_closed(
    provider: str,
    builder: Callable[[dict[str, str]], object],
) -> None:
    del provider
    with pytest.raises(MissingCredentialError):
        builder({})


def test_default_cli_dry_run_does_not_call_network(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def unexpected_network(request: object) -> SmokeReport:
        del request
        raise AssertionError("network execution must not be reached")

    monkeypatch.setattr(newsapi_ai, "execute_minimal_request", unexpected_network)
    assert (
        provider_smoke.main(
            ["--provider", "newsapi_ai", "--query", "technology", "--max-results", "1"]
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)
    assert report["classified_result"] == "BLOCKED"
    assert report["http_status"] is None


def test_env_file_loads_and_os_environment_has_priority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = tmp_path / "provider.env"
    env_file.write_text(
        'NEWSAPI_AI_API_KEY=file-secret\nNEWSAPI_AI_PLAN="file-plan"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("NEWSAPI_AI_API_KEY", "shell-secret")
    monkeypatch.delenv("NEWSAPI_AI_PLAN", raising=False)
    loaded = provider_smoke._load_environment(env_file)
    assert loaded["NEWSAPI_AI_API_KEY"] == "shell-secret"
    assert loaded["NEWSAPI_AI_PLAN"] == "file-plan"


def test_default_root_env_file_is_optional(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    default_env = tmp_path / ".env"
    default_env.write_text("NEWSAPI_AI_PLAN=local-plan\n", encoding="utf-8")
    monkeypatch.setattr(provider_smoke, "DEFAULT_ENV_FILE", default_env)
    monkeypatch.delenv("NEWSAPI_AI_PLAN", raising=False)
    assert provider_smoke._load_environment(None)["NEWSAPI_AI_PLAN"] == "local-plan"


def test_env_file_secret_is_not_printed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    env_file = tmp_path / "provider.env"
    env_file.write_text(f"MARKETAUX_API_TOKEN={SECRET}\n", encoding="utf-8")
    safe_report = SmokeReport(
        provider="marketaux",
        endpoint_family=marketaux.ENDPOINT,
        http_status=200,
        valid_json=True,
        top_level_fields=["data"],
        item_fields=["uuid"],
        result_count=1,
        rate_limit_headers_present=[],
        retry_after_present=False,
        classified_result="PASS",
    )
    monkeypatch.setattr(
        marketaux,
        "execute_minimal_request",
        lambda request: safe_report,
    )
    assert (
        provider_smoke.main(
            [
                "--provider",
                "marketaux",
                "--env-file",
                str(env_file),
                "--execute",
            ]
        )
        == 0
    )
    assert SECRET not in capsys.readouterr().out
    assert SECRET not in caplog.text


def test_execute_report_does_not_print_or_log_key(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("MARKETAUX_API_TOKEN", SECRET)
    safe_report = SmokeReport(
        provider="marketaux",
        endpoint_family=marketaux.ENDPOINT,
        http_status=200,
        valid_json=True,
        top_level_fields=["data", "meta"],
        item_fields=["uuid"],
        result_count=1,
        rate_limit_headers_present=[],
        retry_after_present=False,
        classified_result="PASS",
    )
    monkeypatch.setattr(
        marketaux,
        "execute_minimal_request",
        lambda request: safe_report,
    )
    assert (
        provider_smoke.main(
            [
                "--provider",
                "marketaux",
                "--query",
                "technology",
                "--limit",
                "1",
                "--execute",
            ]
        )
        == 0
    )
    assert SECRET not in capsys.readouterr().out
    assert SECRET not in caplog.text


def test_newsapi_execute_is_future_blocked_without_network(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("NEWSAPI_AI_API_KEY", SECRET)

    def unexpected_network(request: object) -> SmokeReport:
        del request
        raise AssertionError("future/blocked provider must not execute")

    monkeypatch.setattr(newsapi_ai, "execute_minimal_request", unexpected_network)
    assert (
        provider_smoke.main(
            [
                "--provider",
                "newsapi_ai",
                "--query",
                "technology",
                "--max-results",
                "1",
                "--execute",
            ]
        )
        == 2
    )
    report = json.loads(capsys.readouterr().out)
    assert report["classified_result"] == "BLOCKED"
    assert SECRET not in json.dumps(report)


def test_optional_metadata_does_not_block_minimal_marketaux_smoke(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MARKETAUX_API_TOKEN", SECRET)
    for name in (
        "MARKETAUX_PLAN",
        "MARKETAUX_DAILY_LIMIT",
        "MARKETAUX_ALLOWED_RETENTION",
        "MARKETAUX_INTERNAL_AI_ALLOWED",
    ):
        monkeypatch.delenv(name, raising=False)
    safe_report = SmokeReport(
        provider="marketaux",
        endpoint_family=marketaux.ENDPOINT,
        http_status=200,
        valid_json=True,
        top_level_fields=["data", "meta"],
        item_fields=["uuid"],
        result_count=1,
        rate_limit_headers_present=[],
        retry_after_present=False,
        classified_result="PASS",
    )
    monkeypatch.setattr(marketaux, "execute_minimal_request", lambda request: safe_report)
    assert (
        provider_smoke.main(
            [
                "--provider",
                "marketaux",
                "--query",
                "technology",
                "--limit",
                "1",
                "--execute",
            ]
        )
        == 0
    )


def test_cli_does_not_accept_key_argument() -> None:
    with pytest.raises(SystemExit):
        provider_smoke.main(["--provider", "newsapi_ai", "--api-key", SECRET])


def test_redaction_never_contains_secrets() -> None:
    requests = [
        newsapi_ai.build_request(
            {"NEWSAPI_AI_API_KEY": SECRET},
            query="technology",
            max_results=1,
            execute=True,
        ),
        marketaux.build_request(
            {"MARKETAUX_API_TOKEN": SECRET},
            query="technology",
            limit=1,
            execute=True,
        ),
        finnhub.build_request(
            {"FINNHUB_API_KEY": SECRET},
            symbol="AAPL",
            execute=True,
        ),
        eia.build_request(
            {"EIA_API_KEY": SECRET, "EIA_API_VERSION": "v2"},
            dataset="electricity",
            limit=1,
            execute=True,
        ),
        sec_edgar.build_request(
            {"SEC_USER_AGENT": SECRET, "SEC_CONTACT_EMAIL": "private@example.invalid"},
            ticker="AAPL",
            execute=True,
        ),
    ]
    redacted = json.dumps(
        [
            newsapi_ai.redact_request(requests[0]),
            marketaux.redact_request(requests[1]),
            finnhub.redact_request(requests[2]),
            eia.redact_request(requests[3]),
            sec_edgar.redact_request(requests[4]),
        ]
    )
    assert SECRET not in redacted
    assert "private@example.invalid" not in redacted


def test_summary_contains_shape_not_values() -> None:
    payload = {
        "meta": {"returned": 1},
        "data": [
            {
                "title": "REAL TITLE MUST NOT LEAK",
                "url": "https://source.invalid/private",
                "description": "REAL BODY MUST NOT LEAK",
            }
        ],
    }
    request = marketaux.build_request(
        {"MARKETAUX_API_TOKEN": SECRET},
        query="technology",
        limit=1,
        execute=True,
    )
    transport = httpx.MockTransport(
        lambda incoming: _response(
            payload,
            {
                "Retry-After": "10",
                "X-RateLimit-Remaining": "20",
            },
        )
    )
    report = marketaux.execute_minimal_request(request, transport=transport)
    serialized = json.dumps(report.as_dict())
    assert "REAL TITLE MUST NOT LEAK" not in serialized
    assert "https://source.invalid/private" not in serialized
    assert "REAL BODY MUST NOT LEAK" not in serialized
    assert SECRET not in serialized
    assert set(report.as_dict()) == {
        "provider",
        "endpoint_family",
        "http_status",
        "valid_json",
        "top_level_fields",
        "item_fields",
        "result_count",
        "rate_limit_headers_present",
        "retry_after_present",
        "classified_result",
    }
    assert report.item_fields == ["description", "title", "url"]
    assert report.result_count == 1
    assert report.retry_after_present is True


def test_official_request_contracts() -> None:
    news_request = newsapi_ai.build_request(
        {"NEWSAPI_AI_API_KEY": SECRET},
        query="technology",
        max_results=1,
        execute=True,
    )
    assert news_request.url == "https://eventregistry.org/api/v1/article/getArticles"
    assert news_request.method == "POST"
    assert news_request.json_body is not None
    assert news_request.json_body["articlesCount"] == 1

    marketaux_request = marketaux.build_request(
        {"MARKETAUX_API_TOKEN": SECRET},
        query="technology",
        limit=1,
        execute=True,
    )
    assert marketaux_request.url == "https://api.marketaux.com/v1/news/all"
    assert marketaux_request.params["limit"] == 1

    finnhub_request = finnhub.build_request(
        {"FINNHUB_API_KEY": SECRET},
        symbol="AAPL",
        execute=True,
    )
    assert finnhub_request.url == "https://finnhub.io/api/v1/quote"
    assert finnhub_request.headers["X-Finnhub-Token"] == SECRET
    assert "token" not in finnhub_request.params

    eia_request = eia.build_request(
        {"EIA_API_KEY": SECRET, "EIA_API_VERSION": "v2"},
        dataset="electricity",
        limit=1,
        execute=True,
    )
    assert eia_request.url == "https://api.eia.gov/v2/electricity/retail-sales/data/"
    assert eia_request.params["api_key"] == SECRET
    assert eia_request.params["data[]"] == "price"
    assert "data[0]" not in eia_request.params
    assert eia_request.params["length"] == 1

    sec_request = sec_edgar.build_request(
        {"SEC_USER_AGENT": "news-collect", "SEC_CONTACT_EMAIL": "owner@example.invalid"},
        ticker="AAPL",
        execute=True,
    )
    assert sec_request.url == "https://data.sec.gov/submissions/CIK0000320193.json"
    assert sec_request.headers["User-Agent"] == "news-collect owner@example.invalid"


def test_sec_columnar_summary_records_fields_and_count_only() -> None:
    payload = {
        "name": "REAL COMPANY VALUE",
        "filings": {
            "recent": {
                "accessionNumber": ["0001"],
                "primaryDocument": ["secret-document.htm"],
            }
        },
    }
    top, item, count = sec_edgar.summarize_response_shape(payload)
    assert top == ["filings", "name"]
    assert item == ["accessionNumber", "primaryDocument"]
    assert count == 1
    assert "REAL COMPANY VALUE" not in json.dumps([top, item, count])


@pytest.mark.parametrize(
    ("module", "request_spec", "payload"),
    [
        (
            newsapi_ai,
            newsapi_ai.build_request(
                {"NEWSAPI_AI_API_KEY": SECRET},
                query="technology",
                max_results=1,
                execute=True,
            ),
            {"articles": {"results": [{"uri": "hidden", "title": "hidden"}]}},
        ),
        (
            marketaux,
            marketaux.build_request(
                {"MARKETAUX_API_TOKEN": SECRET},
                query="technology",
                limit=1,
                execute=True,
            ),
            {"meta": {"returned": 1}, "data": [{"uuid": "hidden"}]},
        ),
        (
            finnhub,
            finnhub.build_request(
                {"FINNHUB_API_KEY": SECRET},
                symbol="AAPL",
                execute=True,
            ),
            {"c": 1.0, "t": 1},
        ),
        (
            eia,
            eia.build_request(
                {"EIA_API_KEY": SECRET, "EIA_API_VERSION": "v2"},
                dataset="electricity",
                limit=1,
                execute=True,
            ),
            {"response": {"data": [{"period": "hidden", "price": "hidden"}]}},
        ),
        (
            sec_edgar,
            sec_edgar.build_request(
                {
                    "SEC_USER_AGENT": "news-collect",
                    "SEC_CONTACT_EMAIL": "owner@example.invalid",
                },
                ticker="AAPL",
                execute=True,
            ),
            {"filings": {"recent": {"accessionNumber": ["hidden"], "form": ["10-K"]}}},
        ),
    ],
)
def test_provider_schema_aware_success(
    module: object,
    request_spec: object,
    payload: object,
) -> None:
    transport = httpx.MockTransport(lambda incoming: _response(payload))
    report = module.execute_minimal_request(request_spec, transport=transport)
    assert report.classified_result == "PASS"


@pytest.mark.parametrize(
    "payload",
    [
        {"meta": {"returned": 0}},
        {"meta": {"returned": 0}, "data": []},
        {"meta": {"returned": 1}, "data": [{}]},
    ],
)
def test_2xx_json_without_list_item_shape_cannot_pass(payload: object) -> None:
    request = marketaux.build_request(
        {"MARKETAUX_API_TOKEN": SECRET},
        query="technology",
        limit=1,
        execute=True,
    )
    transport = httpx.MockTransport(lambda incoming: _response(payload))
    report = marketaux.execute_minimal_request(request, transport=transport)
    assert report.classified_result == "FAIL"


def test_finnhub_requires_quote_candidate_fields() -> None:
    request = finnhub.build_request(
        {"FINNHUB_API_KEY": SECRET},
        symbol="AAPL",
        execute=True,
    )
    transport = httpx.MockTransport(lambda incoming: _response({"unexpected": 1}))
    report = finnhub.execute_minimal_request(request, transport=transport)
    assert report.classified_result == "FAIL"


def test_sec_requires_recent_filing_candidate_fields() -> None:
    request = sec_edgar.build_request(
        {"SEC_USER_AGENT": "news-collect", "SEC_CONTACT_EMAIL": "owner@example.invalid"},
        ticker="AAPL",
        execute=True,
    )
    transport = httpx.MockTransport(
        lambda incoming: _response({"filings": {"recent": {"unexpected": ["hidden"]}}})
    )
    report = sec_edgar.execute_minimal_request(request, transport=transport)
    assert report.classified_result == "FAIL"
