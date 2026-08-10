"""Reconcile evidence constraints added after the original 0003 deployment."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COMPOSITE_INDEX = "uq_raw_items_id_source_id"
_COMPOSITE_FK = "fk_evidence_items_raw_item_source"
_SECRET_CHECK = "ck_evidence_items_raw_payload_reference_no_secret_markers"


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)

    unsafe_references = connection.scalar(
        sa.text(
            "SELECT count(*) FROM evidence_items WHERE raw_payload_reference IS NOT NULL AND ("
            "lower(raw_payload_reference) LIKE '%api_key=%' OR "
            "lower(raw_payload_reference) LIKE '%api_token=%' OR "
            "lower(raw_payload_reference) LIKE '%token=%' OR "
            "lower(raw_payload_reference) LIKE '%authorization%' OR "
            "lower(raw_payload_reference) LIKE '%x-finnhub-token%')"
        )
    )
    if unsafe_references:
        raise RuntimeError("unsafe_evidence_reference_blocks_migration")

    provenance_mismatches = connection.scalar(
        sa.text(
            "SELECT count(*) FROM evidence_items e "
            "LEFT JOIN raw_items r ON r.id = e.raw_item_id "
            "WHERE r.id IS NULL OR r.source_id <> e.source_id"
        )
    )
    if provenance_mismatches:
        raise RuntimeError("evidence_provenance_mismatch_blocks_migration")

    raw_indexes = {item["name"] for item in inspector.get_indexes("raw_items")}
    if _COMPOSITE_INDEX not in raw_indexes:
        op.create_index(
            _COMPOSITE_INDEX,
            "raw_items",
            ["id", "source_id"],
            unique=True,
        )

    foreign_keys = inspector.get_foreign_keys("evidence_items")
    composite_exists = any(item["name"] == _COMPOSITE_FK for item in foreign_keys)
    if not composite_exists:
        legacy = next(
            (
                item
                for item in foreign_keys
                if item["referred_table"] == "raw_items"
                and item["constrained_columns"] == ["raw_item_id"]
            ),
            None,
        )
        if legacy is not None and legacy["name"] is not None:
            op.drop_constraint(legacy["name"], "evidence_items", type_="foreignkey")
        op.create_foreign_key(
            _COMPOSITE_FK,
            "evidence_items",
            "raw_items",
            ["raw_item_id", "source_id"],
            ["id", "source_id"],
            ondelete="RESTRICT",
        )

    check_names = {item["name"] for item in inspector.get_check_constraints("evidence_items")}
    if _SECRET_CHECK not in check_names:
        op.create_check_constraint(
            _SECRET_CHECK,
            "evidence_items",
            "raw_payload_reference IS NULL OR ("
            "lower(raw_payload_reference) NOT LIKE '%api_key=%' AND "
            "lower(raw_payload_reference) NOT LIKE '%api_token=%' AND "
            "lower(raw_payload_reference) NOT LIKE '%token=%' AND "
            "lower(raw_payload_reference) NOT LIKE '%authorization%' AND "
            "lower(raw_payload_reference) NOT LIKE '%x-finnhub-token%')",
        )


def downgrade() -> None:
    # Current 0003 already defines these constraints. This reconciliation revision repairs
    # databases that applied an earlier 0003 artifact, so downgrade must preserve 0003's
    # current contract instead of reintroducing the historical drift.
    pass
