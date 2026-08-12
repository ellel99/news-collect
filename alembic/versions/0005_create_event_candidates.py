"""Create EventCandidate persistence and reversible Evidence associations."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    status_enum = postgresql.ENUM(
        "candidate",
        "confirmed",
        "rejected",
        name="event_candidate_status",
        create_type=False,
    )
    postgresql.ENUM("candidate", "confirmed", "rejected", name="event_candidate_status").create(
        op.get_bind(), checkfirst=True
    )
    op.create_table(
        "event_candidates",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("cluster_key", sa.CHAR(64), nullable=False),
        sa.Column("anchor_type", sa.String(64), nullable=False),
        sa.Column("anchor_value_hash", sa.CHAR(64), nullable=False),
        sa.Column("strong_identity_hash", sa.CHAR(64), nullable=True),
        sa.Column(
            "identity_signatures",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "title_fingerprints",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("status", status_enum, nullable=False),
        sa.Column("canonical_title", sa.Text(), nullable=True),
        sa.Column("fact_summary", sa.Text(), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("latest_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("primary_entity", sa.String(255), nullable=True),
        sa.Column(
            "entities", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False
        ),
        sa.Column(
            "companies", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False
        ),
        sa.Column(
            "assets", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False
        ),
        sa.Column(
            "sectors", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False
        ),
        sa.Column(
            "topics", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False
        ),
        sa.Column("evidence_count", sa.Integer(), nullable=False),
        sa.Column("source_count", sa.Integer(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("importance_score", sa.Float(), nullable=False),
        sa.Column(
            "importance_reasons",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "cluster_key ~ '^[0-9a-f]{64}$'", name="ck_event_candidates_cluster_key"
        ),
        sa.CheckConstraint(
            "anchor_value_hash ~ '^[0-9a-f]{64}$'", name="ck_event_candidates_anchor_value_hash"
        ),
        sa.CheckConstraint(
            "strong_identity_hash IS NULL OR strong_identity_hash ~ '^[0-9a-f]{64}$'",
            name="ck_event_candidates_strong_identity_hash",
        ),
        sa.CheckConstraint("evidence_count > 0", name="ck_event_candidates_evidence_positive"),
        sa.CheckConstraint("source_count > 0", name="ck_event_candidates_source_positive"),
        sa.CheckConstraint("confidence BETWEEN 0 AND 1", name="ck_event_candidates_confidence"),
        sa.CheckConstraint(
            "importance_score BETWEEN 0 AND 100", name="ck_event_candidates_importance"
        ),
        sa.CheckConstraint(
            "latest_seen_at >= first_seen_at", name="ck_event_candidates_seen_order"
        ),
    )
    op.create_index(
        "uq_event_candidates_cluster_key", "event_candidates", ["cluster_key"], unique=True
    )
    op.create_index(
        "ix_event_candidates_status_latest", "event_candidates", ["status", "latest_seen_at"]
    )
    op.create_index(
        "ix_event_candidates_strong_identity", "event_candidates", ["strong_identity_hash"]
    )
    op.execute(
        """
        CREATE FUNCTION prevent_event_candidate_identity_change() RETURNS trigger AS $$
        BEGIN
          IF NEW.id <> OLD.id OR NEW.cluster_key <> OLD.cluster_key THEN
            RAISE EXCEPTION 'event_candidate_identity_immutable';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_event_candidate_identity_immutable
        BEFORE UPDATE ON event_candidates
        FOR EACH ROW EXECUTE FUNCTION prevent_event_candidate_identity_change()
        """
    )
    op.create_table(
        "event_candidate_evidence",
        sa.Column(
            "event_candidate_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("event_candidates.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column(
            "evidence_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("evidence_items.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("match_rule", sa.String(100), nullable=False),
        sa.Column("rule_version", sa.Integer(), nullable=False),
        sa.Column("official_source", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "added_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("char_length(btrim(match_rule)) > 0", name="ck_event_evidence_rule"),
        sa.CheckConstraint("rule_version > 0", name="ck_event_evidence_rule_version"),
        sa.CheckConstraint(
            "(active AND removed_at IS NULL) OR (NOT active AND removed_at IS NOT NULL)",
            name="ck_event_evidence_active_removed",
        ),
    )
    op.create_index(
        "uq_event_candidate_evidence_pair",
        "event_candidate_evidence",
        ["event_candidate_id", "evidence_item_id"],
        unique=True,
    )
    op.create_index(
        "ix_event_candidate_evidence_item", "event_candidate_evidence", ["evidence_item_id"]
    )
    op.create_index(
        "ix_event_candidate_evidence_active",
        "event_candidate_evidence",
        ["event_candidate_id", "active"],
    )


def downgrade() -> None:
    op.drop_table("event_candidate_evidence")
    op.execute("DROP TRIGGER trg_event_candidate_identity_immutable ON event_candidates")
    op.execute("DROP FUNCTION prevent_event_candidate_identity_change()")
    op.drop_table("event_candidates")
    postgresql.ENUM(name="event_candidate_status").drop(op.get_bind(), checkfirst=True)
