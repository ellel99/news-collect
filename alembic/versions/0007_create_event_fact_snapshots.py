"""Create deterministic Event fact snapshot metadata.

Revision ID: 0007
Revises: 0006
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "event_fact_snapshots",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("event_candidate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fact_version", sa.Integer(), nullable=False),
        sa.Column("snapshot_hash", sa.CHAR(length=64), nullable=False),
        sa.Column("evidence_total_count", sa.Integer(), nullable=False),
        sa.Column("evidence_included_count", sa.Integer(), nullable=False),
        sa.Column("evidence_truncated", sa.Boolean(), nullable=False),
        sa.Column("input_quality", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint("fact_version > 0", name="ck_event_fact_snapshots_version"),
        sa.CheckConstraint("snapshot_hash ~ '^[0-9a-f]{64}$'", name="ck_event_fact_snapshots_hash"),
        sa.CheckConstraint(
            "evidence_total_count >= evidence_included_count AND evidence_included_count > 0",
            name="ck_event_fact_snapshots_counts",
        ),
        sa.CheckConstraint(
            "input_quality IN ('LOW','MEDIUM','HIGH')",
            name="ck_event_fact_snapshots_quality",
        ),
        sa.ForeignKeyConstraint(
            ["event_candidate_id"], ["event_candidates.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_event_fact_snapshots_hash",
        "event_fact_snapshots",
        ["event_candidate_id", "snapshot_hash"],
        unique=True,
    )
    op.create_index(
        "uq_event_fact_snapshots_version",
        "event_fact_snapshots",
        ["event_candidate_id", "fact_version"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("event_fact_snapshots")
