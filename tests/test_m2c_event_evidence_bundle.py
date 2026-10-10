"""Deterministic M2-C relation, quality and digest tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

from market_intelligence.db.models import EventEvidenceBundleStatus, EventEvidenceRelation
from market_intelligence.event_evidence.service import (
    _bundle_material,
    _Classified,
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
        item.prepared.association_id: item.relation
        for item in _classify([contradiction, duplicate, first], {})
    }
    assert relations == {
        first.association_id: EventEvidenceRelation.DUPLICATE,
        duplicate.association_id: EventEvidenceRelation.DUPLICATE,
        contradiction.association_id: EventEvidenceRelation.CONTRADICTING,
    }


def test_relation_groups_handle_a_b_b_and_are_order_independent() -> None:
    rows = [
        _prepared(packet_digest="1" * 64, identity="a" * 64, value="a" * 64),
        _prepared(packet_digest="2" * 64, identity="a" * 64, value="b" * 64),
        _prepared(packet_digest="3" * 64, identity="a" * 64, value="b" * 64),
    ]
    expected = [
        EventEvidenceRelation.CONTRADICTING,
        EventEvidenceRelation.DUPLICATE,
        EventEvidenceRelation.DUPLICATE,
    ]
    for ordering in (rows, list(reversed(rows)), [rows[1], rows[0], rows[2]]):
        relations = {
            item.prepared.packet.packet_digest: item.relation for item in _classify(ordering, {})
        }
        assert [relations[row.packet.packet_digest] for row in rows] == expected


def test_relation_groups_handle_a_a_b_b_and_three_values() -> None:
    rows = [
        _prepared(packet_digest=f"{index}" * 64, identity="a" * 64, value=value * 64)
        for index, value in (("1", "a"), ("2", "a"), ("3", "b"), ("4", "b"), ("5", "c"))
    ]
    classified = _classify(rows, {})
    relations = {item.prepared.packet.packet_digest: item.relation for item in classified}
    assert [relations[row.packet.packet_digest] for row in rows] == [
        EventEvidenceRelation.DUPLICATE,
        EventEvidenceRelation.DUPLICATE,
        EventEvidenceRelation.DUPLICATE,
        EventEvidenceRelation.DUPLICATE,
        EventEvidenceRelation.CONTRADICTING,
    ]
    assert all(item.identity_conflict for item in classified)
    duplicate_flags = {
        item.prepared.packet.packet_digest: item.value_duplicate for item in classified
    }
    assert [duplicate_flags[row.packet.packet_digest] for row in rows] == [
        True,
        True,
        True,
        True,
        False,
    ]


def test_superseding_precedes_value_group_relation_without_changing_peers() -> None:
    changed = _prepared(packet_digest="2" * 64, identity="a" * 64, value="b" * 64)
    peer = _prepared(packet_digest="3" * 64, identity="a" * 64, value="b" * 64)
    baseline = _prepared(packet_digest="1" * 64, identity="a" * 64, value="a" * 64)
    prior = SimpleNamespace(packet_digest="0" * 64)
    relations = {
        item.prepared.packet.packet_digest: item.relation
        for item in _classify([peer, changed, baseline], {changed.association_id: prior})
    }
    assert relations == {
        "1" * 64: EventEvidenceRelation.CONTRADICTING,
        "2" * 64: EventEvidenceRelation.SUPERSEDING,
        "3" * 64: EventEvidenceRelation.DUPLICATE,
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
    assert _classify([row], cast(Any, {row.association_id: prior}))[0].relation is (
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
    rows = [_Classified(row, EventEvidenceRelation.SUPPORTING, False, False)]
    material = _bundle_material(event_id, "ready", (), rows)
    assert _digest(material) == _digest(material)
    assert _digest(material) != _digest(_bundle_material(uuid.uuid4(), "ready", (), rows))
