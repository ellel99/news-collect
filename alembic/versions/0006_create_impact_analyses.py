"""Create versioned ImpactAnalysis persistence.

Revision ID: 0006
Revises: 0005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    status_enum = postgresql.ENUM("valid", "retry", "failed", name="impact_analysis_status")
    status_enum.create(op.get_bind(), checkfirst=True)
    column_enum = postgresql.ENUM(name="impact_analysis_status", create_type=False)
    op.create_table(
        "impact_analyses",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "event_candidate_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("event_candidates.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("analysis_version", sa.Integer(), nullable=False),
        sa.Column("fact_version", sa.Integer(), nullable=False),
        sa.Column("fact_snapshot_hash", sa.CHAR(64), nullable=False),
        sa.Column("analyzer_provider", sa.String(100), nullable=False),
        sa.Column("analyzer_model", sa.String(150), nullable=False),
        sa.Column("analyzer_contract_version", sa.Integer(), nullable=False),
        sa.Column(
            "affected_companies",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "affected_assets",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "affected_sectors",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("impact_direction", sa.String(32), nullable=False),
        sa.Column("impact_horizon", sa.String(32), nullable=False),
        sa.Column(
            "impact_channels",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("rationale_summary", sa.Text(), nullable=False),
        sa.Column(
            "uncertainty", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False
        ),
        sa.Column("required_market_validation", sa.Boolean(), nullable=False),
        sa.Column("status", column_enum, nullable=False),
        sa.Column(
            "supersedes_analysis_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("impact_analyses.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "safe_errors", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint("analysis_version > 0", name="ck_impact_analyses_version_positive"),
        sa.CheckConstraint("fact_version > 0", name="ck_impact_analyses_fact_version_positive"),
        sa.CheckConstraint(
            "fact_snapshot_hash ~ '^[0-9a-f]{64}$'", name="ck_impact_analyses_fact_hash"
        ),
        sa.CheckConstraint("confidence BETWEEN 0 AND 1", name="ck_impact_analyses_confidence"),
        sa.CheckConstraint(
            "impact_direction IN ('positive','negative','mixed','uncertain')",
            name="ck_impact_analyses_direction",
        ),
        sa.CheckConstraint(
            "impact_horizon IN ('immediate','short_term','medium_term','long_term')",
            name="ck_impact_analyses_horizon",
        ),
    )
    op.create_index(
        "uq_impact_analyses_idempotency",
        "impact_analyses",
        [
            "event_candidate_id",
            "fact_snapshot_hash",
            "analyzer_provider",
            "analyzer_model",
            "analyzer_contract_version",
        ],
        unique=True,
    )
    op.create_index(
        "uq_impact_analyses_event_version",
        "impact_analyses",
        ["event_candidate_id", "analysis_version"],
        unique=True,
    )
    op.create_index(
        "ix_impact_analyses_event_created",
        "impact_analyses",
        ["event_candidate_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("impact_analyses")
    postgresql.ENUM(name="impact_analysis_status").drop(op.get_bind(), checkfirst=True)
