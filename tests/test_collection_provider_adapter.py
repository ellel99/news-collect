from pathlib import Path


def test_provider_collection_integration_has_no_forbidden_dependencies() -> None:
    root = Path(__file__).parents[1] / "src" / "market_intelligence" / "collection"
    source = "\n".join((root / name).read_text() for name in ("provider_adapter.py", "runner.py"))
    forbidden = (
        "EvidenceWriteService",
        "evidence_items",
        "provider_mappings",
        "provider_capture",
        "local_evaluation",
        "import requests",
        "import httpx",
        "OpenAI",
        "Telegram",
        "Recommendation",
    )
    assert all(marker not in source for marker in forbidden)
