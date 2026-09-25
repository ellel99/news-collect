"""Shared provider identity normalization without runtime or package side effects."""

import re

_MARKETAUX_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,254}")


def normalize_marketaux_provider_identity(value: object) -> str:
    """Normalize the approved Marketaux identity alphabet shared by every layer."""
    if not isinstance(value, str):
        raise ValueError("marketaux_provider_identity_invalid")
    normalized = value.strip()
    if not _MARKETAUX_IDENTITY.fullmatch(normalized):
        raise ValueError("marketaux_provider_identity_invalid")
    return normalized
