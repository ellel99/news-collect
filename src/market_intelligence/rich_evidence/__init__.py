"""Provider-neutral Rich Evidence Packet read boundary."""

from market_intelligence.rich_evidence.builder import (
    RichEvidencePacketBuilder,
    canonical_packet_bytes,
)
from market_intelligence.rich_evidence.contracts import (
    PacketPage,
    RichEvidenceError,
    RichEvidencePacket,
)

__all__ = [
    "PacketPage",
    "RichEvidenceError",
    "RichEvidencePacket",
    "RichEvidencePacketBuilder",
    "canonical_packet_bytes",
]
