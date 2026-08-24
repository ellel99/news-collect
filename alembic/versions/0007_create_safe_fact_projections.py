"""Create durable R2 raw observations and safe factual projections."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    postgresql.ENUM(
        "first_seen",
        "duplicate_same_projection",
        "revision_candidate",
        name="raw_item_observation_kind",
    ).create(bind, checkfirst=True)
    postgresql.ENUM("complete", "partial", "blocked", name="safe_projection_quality_status").create(
        bind, checkfirst=True
    )
    postgresql.ENUM(
        "pending",
        "validating",
        "ready",
        "retry",
        "blocked",
        name="safe_projection_processing_status",
    ).create(bind, checkfirst=True)

    op.create_table(
        "raw_item_observations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "collection_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("collection_runs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "raw_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("raw_items.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "target_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("collection_targets.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sources.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "source_account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("source_accounts.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("operation_key", sa.String(100), nullable=False),
        sa.Column("config_revision", sa.BigInteger(), nullable=True),
        sa.Column("provider_contract_version", sa.SmallInteger(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("projection_hash", sa.String(64), nullable=False),
        sa.Column(
            "observation_kind",
            postgresql.ENUM(name="raw_item_observation_kind", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "collection_run_id", "raw_item_id", name="uq_raw_item_observations_run_item"
        ),
        sa.CheckConstraint(
            "char_length(projection_hash)=64",
            name="ck_raw_item_observations_projection_hash",
        ),
        sa.CheckConstraint(
            "target_id IS NOT NULL OR config_revision IS NULL",
            name="ck_raw_item_observations_legacy_revision",
        ),
    )
    op.create_index(
        "ix_raw_item_observations_raw_created",
        "raw_item_observations",
        ["raw_item_id", "created_at"],
    )
    op.create_index(
        "ix_raw_item_observations_target_observed",
        "raw_item_observations",
        ["target_id", "observed_at"],
    )

    op.create_table(
        "safe_fact_projections",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "observation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("raw_item_observations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "raw_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("raw_items.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("operation_key", sa.String(100), nullable=False),
        sa.Column("projection_schema_version", sa.SmallInteger(), nullable=False),
        sa.Column("factual_payload", postgresql.JSONB(), nullable=False),
        sa.Column("projection_hash", sa.String(64), nullable=False),
        sa.Column(
            "quality_status",
            postgresql.ENUM(name="safe_projection_quality_status", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "processing_status",
            postgresql.ENUM(name="safe_projection_processing_status", create_type=False),
            nullable=False,
        ),
        sa.Column("safe_error_code", sa.String(100), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint(
            "observation_id",
            "projection_schema_version",
            name="uq_safe_fact_projections_observation_version",
        ),
        sa.CheckConstraint(
            "projection_schema_version > 0",
            name="ck_safe_fact_projections_schema_version",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(factual_payload)='object'",
            name="ck_safe_fact_projections_payload_object",
        ),
        sa.CheckConstraint(
            "char_length(projection_hash)=64",
            name="ck_safe_fact_projections_hash",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0", name="ck_safe_fact_projections_attempt_nonnegative"
        ),
        sa.CheckConstraint(
            "processing_status <> 'ready' OR "
            "(safe_error_code IS NULL AND processed_at IS NOT NULL)",
            name="ck_safe_fact_projections_ready_valid",
        ),
    )
    op.create_index(
        "ix_safe_fact_projections_claim",
        "safe_fact_projections",
        ["processing_status", "next_retry_at", "created_at"],
    )
    op.create_index(
        "ix_safe_fact_projections_raw_created",
        "safe_fact_projections",
        ["raw_item_id", "created_at"],
    )

    op.execute("""
    CREATE FUNCTION r2_observation_provenance_guard() RETURNS trigger AS $$
    DECLARE r collection_runs%ROWTYPE;
            i raw_items%ROWTYPE;
            t collection_targets%ROWTYPE;
            s sources%ROWTYPE;
    BEGIN
      SELECT * INTO r FROM collection_runs WHERE id=NEW.collection_run_id;
      SELECT * INTO i FROM raw_items WHERE id=NEW.raw_item_id;
      SELECT * INTO s FROM sources WHERE id=NEW.source_id;
      IF NOT FOUND OR r.id IS NULL OR i.id IS NULL
         OR NEW.source_id IS DISTINCT FROM r.source_id
         OR NEW.source_account_id IS DISTINCT FROM r.source_account_id
         OR NEW.source_id IS DISTINCT FROM i.source_id
         OR NEW.source_account_id IS DISTINCT FROM i.source_account_id
         OR NEW.provider IS DISTINCT FROM s.access_method THEN
        RAISE EXCEPTION 'raw_item_observation_provenance_mismatch';
      END IF;
      IF NEW.target_id IS NOT NULL THEN
        SELECT * INTO t FROM collection_targets WHERE id=NEW.target_id;
        IF NOT FOUND OR r.target_id IS DISTINCT FROM NEW.target_id
           OR NEW.source_id IS DISTINCT FROM t.source_id
           OR NEW.source_account_id IS DISTINCT FROM t.source_account_id
           OR NEW.operation_key IS DISTINCT FROM t.operation_key
           OR NEW.config_revision IS DISTINCT FROM t.config_revision
           OR NEW.provider_contract_version IS DISTINCT FROM t.provider_contract_version THEN
          RAISE EXCEPTION 'raw_item_observation_target_mismatch';
        END IF;
      END IF;
      RETURN NEW;
    END; $$ LANGUAGE plpgsql
    """)
    op.execute("""
    CREATE TRIGGER trg_r2_observation_provenance_guard BEFORE INSERT OR UPDATE
      ON raw_item_observations FOR EACH ROW EXECUTE FUNCTION r2_observation_provenance_guard()
    """)
    op.execute("""
    CREATE FUNCTION r2_projection_provenance_guard() RETURNS trigger AS $$
    DECLARE o raw_item_observations%ROWTYPE;
    BEGIN
      SELECT * INTO o FROM raw_item_observations WHERE id=NEW.observation_id;
      IF NOT FOUND OR NEW.raw_item_id IS DISTINCT FROM o.raw_item_id
         OR NEW.provider IS DISTINCT FROM o.provider
         OR NEW.operation_key IS DISTINCT FROM o.operation_key
         OR NEW.projection_hash IS DISTINCT FROM o.projection_hash THEN
        RAISE EXCEPTION 'safe_fact_projection_provenance_mismatch';
      END IF;
      RETURN NEW;
    END; $$ LANGUAGE plpgsql
    """)
    op.execute("""
    CREATE TRIGGER trg_r2_projection_provenance_guard BEFORE INSERT OR UPDATE OF
      observation_id,raw_item_id,provider,operation_key,projection_hash
      ON safe_fact_projections FOR EACH ROW EXECUTE FUNCTION r2_projection_provenance_guard()
    """)


def downgrade() -> None:
    bind = op.get_bind()
    projection_count = bind.execute(
        sa.text("SELECT count(*) FROM safe_fact_projections")
    ).scalar_one()
    observation_count = bind.execute(
        sa.text("SELECT count(*) FROM raw_item_observations")
    ).scalar_one()
    if projection_count or observation_count:
        raise RuntimeError("migration_0007_downgrade_requires_empty_r2_state")
    op.execute("DROP TRIGGER trg_r2_projection_provenance_guard ON safe_fact_projections")
    op.execute("DROP FUNCTION r2_projection_provenance_guard()")
    op.execute("DROP TRIGGER trg_r2_observation_provenance_guard ON raw_item_observations")
    op.execute("DROP FUNCTION r2_observation_provenance_guard()")
    op.drop_table("safe_fact_projections")
    op.drop_table("raw_item_observations")
    postgresql.ENUM(name="safe_projection_processing_status").drop(bind, checkfirst=True)
    postgresql.ENUM(name="safe_projection_quality_status").drop(bind, checkfirst=True)
    postgresql.ENUM(name="raw_item_observation_kind").drop(bind, checkfirst=True)
