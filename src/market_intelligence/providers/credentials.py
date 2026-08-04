"""Opaque runtime credentials that never read environment or persist secrets."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class RuntimeCredential:
    """Explicitly injected secret with a redacted representation."""

    name: str
    _value: str = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.name or not self._value:
            raise ValueError("runtime_credential_missing")
        if any(character in self._value for character in ("\r", "\n")):
            raise ValueError("runtime_credential_invalid")

    def reveal_for_transport(self) -> str:
        """Reveal only at the final HTTP transport boundary."""

        return self._value

    def __repr__(self) -> str:
        return f"RuntimeCredential(name={self.name!r}, value=<redacted>)"
