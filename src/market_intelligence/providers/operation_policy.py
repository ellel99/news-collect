"""Static operation-specific factual and downstream policy, without runtime I/O."""

from dataclasses import dataclass


@dataclass(frozen=True)
class FactualOperationPolicy:
    item_type: str
    evidence_kind: str
    source_type: str
    access: str
    content: str | None
    retention: frozenset[str]


POLICIES = {
    ("marketaux", "news_all"): FactualOperationPolicy(
        "marketaux_news",
        "news",
        "news",
        "link_only",
        "article",
        frozenset({"link_only", "metadata_only"}),
    ),
    ("finnhub", "quote"): FactualOperationPolicy(
        "finnhub_quote",
        "market_data",
        "market_data",
        "licensed",
        None,
        frozenset({"metadata_only"}),
    ),
    ("finnhub", "company_news"): FactualOperationPolicy(
        "finnhub_company_news",
        "news",
        "news",
        "licensed",
        "article",
        frozenset({"metadata_only", "link_only"}),
    ),
    ("eia", "electricity_retail_sales"): FactualOperationPolicy(
        "eia_energy_timeseries",
        "energy_official",
        "official_energy",
        "public_summary",
        None,
        frozenset({"metadata_only"}),
    ),
    ("eia", "electricity_rto_region_data"): FactualOperationPolicy(
        "eia_energy_timeseries",
        "energy_official",
        "official_energy",
        "public_summary",
        None,
        frozenset({"metadata_only"}),
    ),
    ("sec_edgar", "submissions_recent"): FactualOperationPolicy(
        "sec_filing",
        "disclosure",
        "disclosure",
        "link_only",
        "official_release",
        frozenset({"link_only", "metadata_only"}),
    ),
}


def factual_operation_policy(
    provider: str, operation: str, contract_version: int | None = None
) -> FactualOperationPolicy:
    versions = (
        {2}
        if operation in {"company_news", "electricity_rto_region_data"}
        else {1}
        if operation == "quote"
        else {1, 2}
    )
    if contract_version is not None and contract_version not in versions:
        raise ValueError("factual_operation_version_unsupported")
    try:
        return POLICIES[provider, operation]
    except KeyError:
        raise ValueError("factual_operation_unsupported") from None
