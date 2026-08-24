"""Worker-only process-environment credential resolution."""

from __future__ import annotations

from collections.abc import Mapping

from market_intelligence.providers.credentials import RuntimeCredential


class CredentialResolutionError(RuntimeError):
    pass


def resolve_runtime_credential(provider: str, environ: Mapping[str, str]) -> RuntimeCredential:
    if provider == "sec_edgar":
        agent, contact = environ.get("SEC_USER_AGENT", ""), environ.get("SEC_CONTACT_EMAIL", "")
        if not agent or not contact:
            raise CredentialResolutionError("provider_runtime_credential_missing")
        return RuntimeCredential("SEC_USER_AGENT", f"{agent} {contact}")
    name = {
        "marketaux": "MARKETAUX_API_TOKEN",
        "finnhub": "FINNHUB_API_KEY",
        "eia": "EIA_API_KEY",
    }.get(provider)
    if name is None:
        raise CredentialResolutionError("provider_credential_contract_unknown")
    value = environ.get(name, "")
    if not value:
        raise CredentialResolutionError("provider_runtime_credential_missing")
    return RuntimeCredential(name, value)
