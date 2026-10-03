"""Deterministic M2-C relation, quality and digest tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

from market_intelligence.db.models import EventEvidenceBundleStatus, EventEvidenceRelation
from market_intelligence.event_evidence.service import (
    _bundle_material,
    _classify,
    _digest,
    _Prepared,
    _quality,
    _same_material,
)


def _prepared(
    *,
    packet_digest: str,
    identity: str,
    value: str,
    quality: str = "complete",
    truncated: bool = False,
) -> _Prepared:
    packet = SimpleNamespace(
        packet_digest=packet_digest,
        quality=quality,
        truncation=SimpleNamespace(truncated=truncated),
        current=SimpleNamespace(projection_hash="f" * 64),
        provider="marketaux",
        operation_key="news_all",
        provenance=SimpleNamespace(source_id=uuid.UUID(int=1)),
        current_published_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    return _Prepared(
        uuid.uuid4(),
        uuid.uuid4(),
        cast(Any, packet),
        identity,
        value,
    )


def test_relations_are_deterministic_and_conflict_is_only_expressed() -> None:
    first = _prepared(packet_digest="1" * 64, identity="a" * 64, value="b" * 64)
    duplicate = _prepared(packet_digest="2" * 64, identity="a" * 64, value="b" * 64)
    contradiction = _prepared(packet_digest="3" * 64, identity="a" * 64, value="c" * 64)
    relations = {
        row.association_id: relation
        for row, relation in _classify([contradiction, duplicate, first], {})
    }
    assert relations == {
        first.association_id: EventEvidenceRelation.SUPPORTING,
        duplicate.association_id: EventEvidenceRelation.DUPLICATE,
        contradiction.association_id: EventEvidenceRelation.CONTRADICTING,
    }


def test_changed_packet_is_superseding_and_identical_material_is_idempotent() -> None:
    row = _prepared(packet_digest="4" * 64, identity="a" * 64, value="b" * 64)
    prior = SimpleNamespace(
        evidence_item_id=row.evidence_id,
        packet_digest="0" * 64,
        projection_hash="f" * 64,
        fact_identity_digest=row.fact_identity_digest,
        fact_value_digest=row.fact_value_digest,
    )
    assert _classify([row], cast(Any, {row.association_id: prior}))[0][1] is (
        EventEvidenceRelation.SUPERSEDING
    )
    prior.packet_digest = row.packet.packet_digest
    assert _same_material([row], cast(Any, {row.association_id: prior})) is True


def test_partial_and_truncated_inputs_have_stable_value_free_reasons() -> None:
    complete = _prepared(packet_digest="1" * 64, identity="a" * 64, value="b" * 64)
    partial = _prepared(
        packet_digest="2" * 64,
        identity="c" * 64,
        value="d" * 64,
        quality="partial",
        truncated=True,
    )
    status, reasons = _quality([complete, partial])
    assert status is EventEvidenceBundleStatus.PARTIAL
    assert reasons == ("event_bundle_packet_truncated", "event_bundle_partial_packet")


def test_bundle_digest_is_stable_and_event_scoped() -> None:
    event_id = uuid.uuid4()
    row = _prepared(packet_digest="1" * 64, identity="a" * 64, value="b" * 64)
    rows = [(row, EventEvidenceRelation.SUPPORTING)]
    material = _bundle_material(event_id, "ready", (), rows)
    assert _digest(material) == _digest(material)
    assert _digest(material) != _digest(_bundle_material(uuid.uuid4(), "ready", (), rows))
