"""Static operation-specific factual and downstream policy, without runtime I/O."""

from dataclasses import dataclass


@dataclass(frozen=True)
class FactualOperationPolicy:
    item_type: str
    evidence_kind: str
    source_type: str
    access: str
    content: str | None


POLICIES = {
    ("marketaux", "news_all"): FactualOperationPolicy(
        "marketaux_news", "news", "news", "link_only", "article"
    ),
    ("finnhub", "quote"): FactualOperationPolicy(
        "finnhub_quote", "market_data", "market_data", "licensed", None
    ),
    ("finnhub", "company_news"): FactualOperationPolicy(
        "finnhub_company_news", "news", "news", "licensed", "article"
    ),
    ("eia", "electricity_retail_sales"): FactualOperationPolicy(
        "eia_energy_timeseries", "energy_official", "official_energy", "public_summary", None
    ),
    ("eia", "electricity_rto_region_data"): FactualOperationPolicy(
        "eia_energy_timeseries", "energy_official", "official_energy", "public_summary", None
    ),
    ("sec_edgar", "submissions_recent"): FactualOperationPolicy(
        "sec_filing", "disclosure", "disclosure", "link_only", "official_release"
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
