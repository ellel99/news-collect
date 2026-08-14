"""Expand the R1 target-owned collection control plane (Migration A)."""

# ruff: noqa: E501

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ENUMS: tuple[postgresql.ENUM, ...] = (
    postgresql.ENUM(
        "draft", "active", "paused", "blocked", "retired", name="collection_target_status"
    ),
    postgresql.ENUM(
        "unknown", "healthy", "degraded", "blocked", name="collection_target_health_status"
    ),
    postgresql.ENUM(
        "strict_incremental",
        "snapshot_watermark",
        "page_token",
        "date_window",
        "compound",
        "revision",
        name="collection_cursor_strategy",
    ),
    postgresql.ENUM("incremental", "snapshot", name="collection_mode"),
    postgresql.ENUM("disabled", "manual_bounded", name="collection_backfill_policy"),
    postgresql.ENUM("ignore", "safe_replace", "reconcile", name="collection_revision_policy"),
    postgresql.ENUM("normal", "backfill", name="collection_run_mode"),
)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.execute(
        sa.text("SELECT count(*) FROM collection_runs WHERE status='running'")
    ).scalar_one():
        raise RuntimeError("migration_a_requires_zero_running_collection_runs")
    provenance_mismatches = bind.execute(
        sa.text("""
        SELECT count(*) FROM raw_items i
        JOIN collection_runs r ON r.id=i.collection_run_id
        WHERE i.source_id IS DISTINCT FROM r.source_id
           OR i.source_account_id IS DISTINCT FROM r.source_account_id
        """)
    ).scalar_one()
    if provenance_mismatches:
        raise RuntimeError("migration_a_historical_provenance_mismatch")
    duplicate_external = bind.execute(
        sa.text("""
        SELECT count(*) FROM (
          SELECT source_id, external_id FROM raw_items
          WHERE external_id IS NOT NULL
          GROUP BY source_id, external_id HAVING count(*) > 1
        ) duplicated
        """)
    ).scalar_one()
    duplicate_hash = bind.execute(
        sa.text("""
        SELECT count(*) FROM (
          SELECT source_id, payload_hash FROM raw_items
          WHERE external_id IS NULL AND payload_hash IS NOT NULL
          GROUP BY source_id, payload_hash HAVING count(*) > 1
        ) duplicated
        """)
    ).scalar_one()
    if duplicate_external or duplicate_hash:
        raise RuntimeError("migration_a_raw_identity_conflict")
    for enum_type in ENUMS:
        enum_type.create(bind, checkfirst=True)
    op.create_unique_constraint(
        "uq_source_accounts_id_source", "source_accounts", ["id", "source_id"]
    )
    op.create_table(
        "collection_targets",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("target_key", sa.String(160), nullable=False),
        sa.Column(
            "source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sources.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("source_account_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("operation_key", sa.String(100), nullable=False),
        sa.Column("legacy_cursor_type", sa.String(100), nullable=True),
        sa.Column("operation_config_version", sa.SmallInteger(), nullable=False),
        sa.Column("provider_contract_version", sa.SmallInteger(), nullable=False),
        sa.Column("config_revision", sa.BigInteger(), server_default="1", nullable=False),
        sa.Column(
            "operation_config",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "status",
            postgresql.ENUM(name="collection_target_status", create_type=False),
            nullable=False,
        ),
        sa.Column("cadence_seconds", sa.Integer(), nullable=False),
        sa.Column("batch_limit", sa.Integer(), nullable=False),
        sa.Column("max_requests_per_run", sa.SmallInteger(), server_default="1", nullable=False),
        sa.Column("max_pages_per_run", sa.SmallInteger(), server_default="1", nullable=False),
        sa.Column("max_response_bytes", sa.Integer(), nullable=False),
        sa.Column("request_timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("max_runtime_seconds", sa.Integer(), nullable=False),
        sa.Column(
            "cursor_strategy",
            postgresql.ENUM(name="collection_cursor_strategy", create_type=False),
            nullable=False,
        ),
        sa.Column("cursor_version", sa.SmallInteger(), server_default="1", nullable=False),
        sa.Column(
            "collection_mode",
            postgresql.ENUM(name="collection_mode", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "backfill_policy",
            postgresql.ENUM(name="collection_backfill_policy", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "revision_policy",
            postgresql.ENUM(name="collection_revision_policy", create_type=False),
            nullable=False,
        ),
        sa.Column("rate_limit_group", sa.String(160), nullable=False),
        sa.Column("priority", sa.SmallInteger(), server_default="100", nullable=False),
        sa.Column("next_due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "health_status",
            postgresql.ENUM(name="collection_target_health_status", create_type=False),
            nullable=False,
        ),
        sa.Column("last_error_code", sa.String(100), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["source_account_id", "source_id"],
            ["source_accounts.id", "source_accounts.source_id"],
            ondelete="RESTRICT",
            name="fk_collection_targets_account_source",
        ),
        sa.CheckConstraint(
            "target_key ~ '^[a-z0-9][a-z0-9._-]{0,159}$'", name="ck_collection_targets_key_format"
        ),
        sa.CheckConstraint("config_revision > 0", name="ck_collection_targets_revision_positive"),
        sa.CheckConstraint(
            "operation_config_version > 0 AND provider_contract_version > 0 AND cursor_version > 0",
            name="ck_collection_targets_versions_positive",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(operation_config) = 'object'", name="ck_collection_targets_config_object"
        ),
        sa.CheckConstraint(
            "cadence_seconds BETWEEN 1 AND 86400", name="ck_collection_targets_cadence"
        ),
        sa.CheckConstraint("batch_limit > 0", name="ck_collection_targets_batch_limit"),
        sa.CheckConstraint(
            "max_requests_per_run BETWEEN 1 AND 20", name="ck_collection_targets_requests"
        ),
        sa.CheckConstraint(
            "max_pages_per_run BETWEEN 1 AND 20", name="ck_collection_targets_pages"
        ),
        sa.CheckConstraint(
            "max_response_bytes BETWEEN 1024 AND 10000000",
            name="ck_collection_targets_response_bytes",
        ),
        sa.CheckConstraint(
            "request_timeout_seconds BETWEEN 1 AND 60", name="ck_collection_targets_request_timeout"
        ),
        sa.CheckConstraint(
            "max_runtime_seconds BETWEEN request_timeout_seconds AND 900",
            name="ck_collection_targets_runtime",
        ),
        sa.CheckConstraint("priority BETWEEN 0 AND 1000", name="ck_collection_targets_priority"),
        sa.CheckConstraint("consecutive_failures >= 0", name="ck_collection_targets_failures"),
        sa.CheckConstraint(
            "(status = 'retired' AND retired_at IS NOT NULL) OR (status <> 'retired' AND retired_at IS NULL)",
            name="ck_collection_targets_retired_at",
        ),
    )
    op.create_index(
        "uq_collection_targets_target_key", "collection_targets", ["target_key"], unique=True
    )
    op.create_index(
        "uq_collection_targets_legacy_owner",
        "collection_targets",
        ["source_account_id", "legacy_cursor_type"],
        unique=True,
        postgresql_where=sa.text("status = 'active' AND legacy_cursor_type IS NOT NULL"),
    )
    op.create_index(
        "ix_collection_targets_source_status", "collection_targets", ["source_id", "status"]
    )
    op.create_index(
        "ix_collection_targets_account_status",
        "collection_targets",
        ["source_account_id", "status"],
    )
    op.create_index(
        "ix_collection_targets_rate_status", "collection_targets", ["rate_limit_group", "status"]
    )
    op.create_index(
        "ix_collection_targets_due",
        "collection_targets",
        ["next_due_at", "priority", "id"],
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "ix_collection_targets_retry",
        "collection_targets",
        ["next_retry_at", "priority", "id"],
        postgresql_where=sa.text("status = 'active' AND next_retry_at IS NOT NULL"),
    )

    op.add_column(
        "collection_runs", sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.add_column(
        "collection_runs",
        sa.Column(
            "run_mode",
            postgresql.ENUM(name="collection_run_mode", create_type=False),
            server_default="normal",
            nullable=False,
        ),
    )
    op.add_column("collection_runs", sa.Column("dispatch_identity", sa.String(255), nullable=True))
    op.create_foreign_key(
        "fk_collection_runs_target",
        "collection_runs",
        "collection_targets",
        ["target_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_collection_runs_target_started", "collection_runs", ["target_id", "started_at"]
    )
    op.create_index(
        "uq_collection_runs_running_target_mode",
        "collection_runs",
        ["target_id", "run_mode"],
        unique=True,
        postgresql_where=sa.text("status = 'running' AND target_id IS NOT NULL"),
    )
    op.create_index(
        "uq_collection_runs_identity_nullsafe",
        "collection_runs",
        ["id", "source_id", "source_account_id"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )
    op.add_column(
        "collection_cursors", sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.add_column(
        "collection_cursors",
        sa.Column("cursor_version", sa.SmallInteger(), server_default="1", nullable=False),
    )
    op.add_column(
        "collection_cursors",
        sa.Column(
            "run_mode",
            postgresql.ENUM(name="collection_run_mode", create_type=False),
            server_default="normal",
            nullable=False,
        ),
    )
    op.add_column(
        "collection_cursors", sa.Column("continuation", postgresql.JSONB(), nullable=True)
    )
    op.add_column(
        "collection_cursors", sa.Column("watermark_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_foreign_key(
        "fk_collection_cursors_target",
        "collection_cursors",
        "collection_targets",
        ["target_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "uq_collection_cursors_target_type_version_mode",
        "collection_cursors",
        ["target_id", "cursor_type", "cursor_version", "run_mode"],
        unique=True,
        postgresql_where=sa.text("target_id IS NOT NULL"),
    )
    op.execute("""
    CREATE FUNCTION r1_operation_config_safe(config_value jsonb) RETURNS boolean AS $$
    DECLARE item record; BEGIN
      IF jsonb_typeof(config_value)='object' THEN
        FOR item IN SELECT entry.key, entry.value FROM jsonb_each(config_value) AS entry LOOP
          IF item.key ~* '(api[_-]?key|api[_-]?token|token|password|secret|authorization|url|endpoint|module|class|import)'
             OR NOT r1_operation_config_safe(item.value) THEN RETURN false; END IF;
        END LOOP;
      ELSIF jsonb_typeof(config_value)='array' THEN
        FOR item IN SELECT element.value FROM jsonb_array_elements(config_value) AS element LOOP
          IF NOT r1_operation_config_safe(item.value) THEN RETURN false; END IF;
        END LOOP;
      ELSIF jsonb_typeof(config_value)='string' THEN
        IF trim(both '"' from config_value::text) ~* '(https?://|api[_-]?key|api[_-]?token|token|password|secret|authorization)'
          THEN RETURN false; END IF;
      END IF;
      RETURN true;
    END; $$ LANGUAGE plpgsql IMMUTABLE
    """)
    op.execute("""
    CREATE FUNCTION r1_operation_config_guard() RETURNS trigger AS $$ BEGIN
      IF NOT r1_operation_config_safe(NEW.operation_config) THEN
        RAISE EXCEPTION 'collection_target_operation_config_unsafe'; END IF;
      RETURN NEW; END; $$ LANGUAGE plpgsql
    """)
    op.execute("""
    CREATE TRIGGER trg_r1_operation_config_guard BEFORE INSERT OR UPDATE OF operation_config
      ON collection_targets FOR EACH ROW EXECUTE FUNCTION r1_operation_config_guard()
    """)
    op.execute("""
    CREATE FUNCTION r1_target_identity_guard() RETURNS trigger AS $$ BEGIN
      IF NEW.target_key IS DISTINCT FROM OLD.target_key OR NEW.source_id IS DISTINCT FROM OLD.source_id
         OR NEW.source_account_id IS DISTINCT FROM OLD.source_account_id OR NEW.operation_key IS DISTINCT FROM OLD.operation_key
         OR NEW.legacy_cursor_type IS DISTINCT FROM OLD.legacy_cursor_type THEN
        RAISE EXCEPTION 'collection_target_identity_immutable'; END IF; RETURN NEW; END; $$ LANGUAGE plpgsql
    """)
    op.execute("""
    CREATE TRIGGER trg_r1_target_identity_guard BEFORE UPDATE ON collection_targets
      FOR EACH ROW EXECUTE FUNCTION r1_target_identity_guard()
    """)
    op.execute("""
    CREATE FUNCTION r1_source_provider_guard() RETURNS trigger AS $$ BEGIN
      IF NEW.access_method IS DISTINCT FROM OLD.access_method AND EXISTS
        (SELECT 1 FROM collection_targets WHERE source_id=OLD.id) THEN RAISE EXCEPTION 'source_access_method_immutable'; END IF;
      RETURN NEW; END; $$ LANGUAGE plpgsql
    """)
    op.execute("""
    CREATE TRIGGER trg_r1_source_provider_guard BEFORE UPDATE ON sources FOR EACH ROW EXECUTE FUNCTION r1_source_provider_guard();
    """)
    op.execute("""
    CREATE FUNCTION r1_active_legacy_identity_guard() RETURNS trigger AS $$ BEGIN
      IF NEW.status='active' AND (NEW.source_account_id IS NULL OR NEW.legacy_cursor_type IS NULL) THEN
        RAISE EXCEPTION 'active_target_requires_legacy_identity'; END IF; RETURN NEW; END; $$ LANGUAGE plpgsql
    """)
    op.execute("""
    CREATE TRIGGER trg_r1_active_legacy_identity_guard BEFORE INSERT OR UPDATE ON collection_targets
      FOR EACH ROW EXECUTE FUNCTION r1_active_legacy_identity_guard()
    """)
    op.execute("""
    CREATE FUNCTION r1_run_identity_guard() RETURNS trigger AS $$ DECLARE t collection_targets%ROWTYPE; BEGIN
      IF NEW.target_id IS NOT NULL THEN
        SELECT * INTO t FROM collection_targets WHERE id=NEW.target_id;
        IF NOT FOUND OR NEW.source_id IS DISTINCT FROM t.source_id
           OR NEW.source_account_id IS DISTINCT FROM t.source_account_id THEN
          RAISE EXCEPTION 'collection_run_target_provenance_mismatch'; END IF;
      END IF;
      IF TG_OP='INSERT' THEN RETURN NEW; END IF;
      IF OLD.target_id IS NULL AND NEW.target_id IS NOT NULL
         AND current_setting('r1.phase2_backfill', true)='on'
         AND NEW.source_id IS NOT DISTINCT FROM OLD.source_id
         AND NEW.source_account_id IS NOT DISTINCT FROM OLD.source_account_id
         AND NEW.run_mode IS NOT DISTINCT FROM OLD.run_mode THEN RETURN NEW; END IF;
      IF NEW.target_id IS DISTINCT FROM OLD.target_id OR NEW.source_id IS DISTINCT FROM OLD.source_id
         OR NEW.source_account_id IS DISTINCT FROM OLD.source_account_id OR NEW.run_mode IS DISTINCT FROM OLD.run_mode THEN
        RAISE EXCEPTION 'collection_run_identity_immutable'; END IF; RETURN NEW; END; $$ LANGUAGE plpgsql
    """)
    op.execute("""
    CREATE TRIGGER trg_r1_run_identity_guard BEFORE INSERT OR UPDATE ON collection_runs FOR EACH ROW EXECUTE FUNCTION r1_run_identity_guard();
    """)
    op.execute("""
    CREATE FUNCTION r1_cursor_identity_guard() RETURNS trigger AS $$ DECLARE t collection_targets%ROWTYPE; BEGIN
      IF NEW.target_id IS NOT NULL THEN
        SELECT * INTO t FROM collection_targets WHERE id=NEW.target_id;
        IF NOT FOUND OR NEW.source_account_id IS DISTINCT FROM t.source_account_id THEN
          RAISE EXCEPTION 'collection_cursor_target_provenance_mismatch'; END IF;
      END IF;
      IF TG_OP='INSERT' THEN RETURN NEW; END IF;
      IF OLD.target_id IS NULL AND NEW.target_id IS NOT NULL
         AND current_setting('r1.phase2_backfill', true)='on'
         AND NEW.source_account_id IS NOT DISTINCT FROM OLD.source_account_id
         AND NEW.cursor_type IS NOT DISTINCT FROM OLD.cursor_type
         AND NEW.cursor_version IS NOT DISTINCT FROM OLD.cursor_version
         AND NEW.run_mode IS NOT DISTINCT FROM OLD.run_mode THEN RETURN NEW; END IF;
      IF NEW.target_id IS DISTINCT FROM OLD.target_id
         OR NEW.source_account_id IS DISTINCT FROM OLD.source_account_id
         OR NEW.cursor_type IS DISTINCT FROM OLD.cursor_type
         OR NEW.cursor_version IS DISTINCT FROM OLD.cursor_version
         OR NEW.run_mode IS DISTINCT FROM OLD.run_mode THEN
        RAISE EXCEPTION 'collection_cursor_identity_immutable'; END IF;
      RETURN NEW; END; $$ LANGUAGE plpgsql
    """)
    op.execute("""
    CREATE TRIGGER trg_r1_cursor_identity_guard BEFORE INSERT OR UPDATE ON collection_cursors
      FOR EACH ROW EXECUTE FUNCTION r1_cursor_identity_guard()
    """)
    op.execute("""
    CREATE FUNCTION r1_raw_run_provenance_guard() RETURNS trigger AS $$ DECLARE r collection_runs%ROWTYPE; BEGIN
      SELECT * INTO r FROM collection_runs WHERE id=NEW.collection_run_id;
      IF NOT FOUND OR NEW.source_id IS DISTINCT FROM r.source_id OR NEW.source_account_id IS DISTINCT FROM r.source_account_id THEN
        RAISE EXCEPTION 'raw_item_run_provenance_mismatch'; END IF; RETURN NEW; END; $$ LANGUAGE plpgsql
    """)
    op.execute("""
    CREATE CONSTRAINT TRIGGER trg_r1_raw_run_provenance_guard AFTER INSERT OR UPDATE OF collection_run_id,source_id,source_account_id
      ON raw_items DEFERRABLE INITIALLY IMMEDIATE FOR EACH ROW EXECUTE FUNCTION r1_raw_run_provenance_guard()
    """)
    op.create_index(
        "uq_raw_items_source_external_identity",
        "raw_items",
        ["source_id", "external_id"],
        unique=True,
        postgresql_where=sa.text("external_id IS NOT NULL"),
    )
    op.create_index(
        "uq_raw_items_source_projection_hash",
        "raw_items",
        ["source_id", "payload_hash"],
        unique=True,
        postgresql_where=sa.text("external_id IS NULL AND payload_hash IS NOT NULL"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    unsafe = bind.execute(
        sa.text("""
      SELECT EXISTS(SELECT 1 FROM collection_runs WHERE target_id IS NOT NULL)
          OR EXISTS(SELECT 1 FROM collection_cursors WHERE target_id IS NOT NULL)
          OR EXISTS(SELECT 1 FROM collection_targets)
    """)
    ).scalar_one()
    if unsafe:
        raise RuntimeError("migration_a_downgrade_unsafe_target_state")
    op.drop_index("uq_raw_items_source_projection_hash", table_name="raw_items")
    op.drop_index("uq_raw_items_source_external_identity", table_name="raw_items")
    for statement in (
        "DROP TRIGGER trg_r1_raw_run_provenance_guard ON raw_items",
        "DROP FUNCTION r1_raw_run_provenance_guard()",
        "DROP TRIGGER IF EXISTS trg_r1_cursor_identity_guard ON collection_cursors",
        "DROP FUNCTION IF EXISTS r1_cursor_identity_guard()",
        "DROP TRIGGER trg_r1_run_identity_guard ON collection_runs",
        "DROP FUNCTION r1_run_identity_guard()",
        "DROP TRIGGER trg_r1_active_legacy_identity_guard ON collection_targets",
        "DROP FUNCTION r1_active_legacy_identity_guard()",
        "DROP TRIGGER trg_r1_source_provider_guard ON sources",
        "DROP FUNCTION r1_source_provider_guard()",
        "DROP TRIGGER trg_r1_target_identity_guard ON collection_targets",
        "DROP FUNCTION r1_target_identity_guard()",
        "DROP TRIGGER trg_r1_operation_config_guard ON collection_targets",
        "DROP FUNCTION r1_operation_config_guard()",
        "DROP FUNCTION r1_operation_config_safe(jsonb)",
    ):
        op.execute(statement)
    op.drop_index("uq_collection_cursors_target_type_version_mode", table_name="collection_cursors")
    op.drop_constraint("fk_collection_cursors_target", "collection_cursors", type_="foreignkey")
    for column in ("watermark_at", "continuation", "run_mode", "cursor_version", "target_id"):
        op.drop_column("collection_cursors", column)
    op.drop_index("uq_collection_runs_identity_nullsafe", table_name="collection_runs")
    op.drop_index("uq_collection_runs_running_target_mode", table_name="collection_runs")
    op.drop_index("ix_collection_runs_target_started", table_name="collection_runs")
    op.drop_constraint("fk_collection_runs_target", "collection_runs", type_="foreignkey")
    for column in ("dispatch_identity", "run_mode", "target_id"):
        op.drop_column("collection_runs", column)
    op.drop_table("collection_targets")
    op.drop_constraint("uq_source_accounts_id_source", "source_accounts", type_="unique")
    for enum_type in reversed(ENUMS):
        enum_type.drop(op.get_bind(), checkfirst=True)
