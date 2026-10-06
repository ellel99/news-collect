"""Create durable M2-C Event Evidence Bundle revisions and reconciliation jobs."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    # Value-free existing-state preflight: active association provenance must be intact.
    broken = bind.execute(
        sa.text("""
      SELECT count(*) FROM event_candidate_evidence a
      LEFT JOIN event_candidates c ON c.id=a.event_candidate_id
      LEFT JOIN evidence_items e ON e.id=a.evidence_item_id
      WHERE a.active AND (c.id IS NULL OR e.id IS NULL OR a.removed_at IS NOT NULL)
    """)
    ).scalar_one()
    if broken:
        raise RuntimeError("migration_0011_active_association_invalid")
    over_budget = bind.execute(
        sa.text("""
      SELECT count(*) FROM (
        SELECT event_candidate_id FROM event_candidate_evidence
        WHERE active GROUP BY event_candidate_id HAVING count(*) > 500
      ) over_limit
    """)
    ).scalar_one()
    if over_budget:
        raise RuntimeError("migration_0011_active_membership_budget_exceeded")
    ambiguous_owner = bind.execute(
        sa.text("""
      SELECT count(*) FROM (
        SELECT evidence_item_id FROM event_candidate_evidence
        WHERE active GROUP BY evidence_item_id HAVING count(*) > 1
      ) ambiguous
    """)
    ).scalar_one()
    if ambiguous_owner:
        raise RuntimeError("migration_0011_active_evidence_owner_ambiguous")

    op.create_index(
        "uq_event_candidate_evidence_active_owner",
        "event_candidate_evidence",
        ["evidence_item_id"],
        unique=True,
        postgresql_where=sa.text("active"),
    )

    op.execute("""
    CREATE FUNCTION m2c_event_membership_budget_guard() RETURNS trigger AS $$
    DECLARE active_count integer;
    BEGIN
      IF TG_OP='UPDATE' AND OLD.event_candidate_id IS DISTINCT FROM NEW.event_candidate_id THEN
        PERFORM pg_advisory_xact_lock(hashtextextended(
          LEAST(OLD.event_candidate_id::text,NEW.event_candidate_id::text),0));
        PERFORM pg_advisory_xact_lock(hashtextextended(
          GREATEST(OLD.event_candidate_id::text,NEW.event_candidate_id::text),0));
      ELSE
        PERFORM pg_advisory_xact_lock(hashtextextended(NEW.event_candidate_id::text,0));
      END IF;
      IF NEW.active THEN
        SELECT count(*) INTO active_count FROM event_candidate_evidence
          WHERE event_candidate_id=NEW.event_candidate_id AND active AND id<>NEW.id;
        IF active_count >= 500 THEN
          RAISE EXCEPTION 'event_candidate_active_membership_budget_exceeded';
        END IF;
      END IF;
      RETURN NEW;
    END $$ LANGUAGE plpgsql
    """)
    op.execute("""CREATE TRIGGER trg_m2c_event_membership_budget
      BEFORE INSERT OR UPDATE OF active,event_candidate_id ON event_candidate_evidence
      FOR EACH ROW EXECUTE FUNCTION m2c_event_membership_budget_guard()""")

    bundle_status = postgresql.ENUM(
        "ready", "partial", name="event_evidence_bundle_status", create_type=False
    )
    relation = postgresql.ENUM(
        "supporting",
        "duplicate",
        "contradicting",
        "superseding",
        name="event_evidence_relation",
        create_type=False,
    )
    job_status = postgresql.ENUM(
        "pending",
        "processing",
        "ready",
        "partial",
        "retry",
        "blocked",
        name="event_evidence_bundle_job_status",
        create_type=False,
    )
    postgresql.ENUM("ready", "partial", name="event_evidence_bundle_status").create(
        bind, checkfirst=True
    )
    postgresql.ENUM(
        "supporting",
        "duplicate",
        "contradicting",
        "superseding",
        name="event_evidence_relation",
    ).create(bind, checkfirst=True)
    postgresql.ENUM(
        "pending",
        "processing",
        "ready",
        "partial",
        "retry",
        "blocked",
        name="event_evidence_bundle_job_status",
    ).create(bind, checkfirst=True)

    op.create_table(
        "event_evidence_bundles",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "event_candidate_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("event_candidates.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("bundle_version", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("status", bundle_status, nullable=False),
        sa.Column("bundle_digest", sa.CHAR(64), nullable=False, unique=True),
        sa.Column("evidence_count", sa.Integer(), nullable=False),
        sa.Column("source_count", sa.Integer(), nullable=False),
        sa.Column("provider_count", sa.Integer(), nullable=False),
        sa.Column("operation_count", sa.Integer(), nullable=False),
        sa.Column("provider_coverage", postgresql.JSONB(), nullable=False),
        sa.Column("operation_coverage", postgresql.JSONB(), nullable=False),
        sa.Column("reason_codes", postgresql.JSONB(), nullable=False),
        sa.Column("first_event_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_event_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint("event_candidate_id", "revision", name="uq_event_bundle_revision"),
        sa.CheckConstraint("revision > 0", name="ck_event_bundle_revision_positive"),
        sa.CheckConstraint("bundle_digest ~ '^[0-9a-f]{64}$'", name="ck_event_bundle_digest"),
        sa.CheckConstraint("evidence_count > 0", name="ck_event_bundle_evidence_positive"),
        sa.CheckConstraint("evidence_count <= 500", name="ck_event_bundle_evidence_budget"),
        sa.CheckConstraint("source_count > 0", name="ck_event_bundle_source_positive"),
        sa.CheckConstraint("provider_count > 0", name="ck_event_bundle_provider_positive"),
        sa.CheckConstraint("operation_count > 0", name="ck_event_bundle_operation_positive"),
        sa.CheckConstraint(
            "last_event_time >= first_event_time", name="ck_event_bundle_time_order"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(provider_coverage)='array'", name="ck_event_bundle_providers_array"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(operation_coverage)='array'", name="ck_event_bundle_operations_array"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(reason_codes)='array'", name="ck_event_bundle_reasons_array"
        ),
        sa.CheckConstraint(
            'reason_codes <@ \'["event_bundle_partial_packet",'
            '"event_bundle_packet_truncated"]\'::jsonb',
            name="ck_event_bundle_reason_allowlist",
        ),
        sa.CheckConstraint(
            "(status='ready' AND reason_codes='[]'::jsonb) OR "
            "(status='partial' AND jsonb_array_length(reason_codes)>0)",
            name="ck_event_bundle_status_reasons",
        ),
    )
    op.create_index(
        "ix_event_bundle_event_revision",
        "event_evidence_bundles",
        ["event_candidate_id", "revision"],
    )
    op.create_table(
        "event_evidence_bundle_items",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "bundle_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("event_evidence_bundles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "event_candidate_evidence_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("event_candidate_evidence.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "evidence_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("evidence_items.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("packet_digest", sa.CHAR(64), nullable=False),
        sa.Column("projection_hash", sa.CHAR(64), nullable=False),
        sa.Column("fact_identity_digest", sa.CHAR(64), nullable=False),
        sa.Column("fact_value_digest", sa.CHAR(64), nullable=False),
        sa.Column("relation", relation, nullable=False),
        sa.Column("value_duplicate", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "identity_conflict", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("relation_rule", sa.String(100), nullable=False),
        sa.Column("rule_version", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("operation_key", sa.String(100), nullable=False),
        sa.Column(
            "source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sources.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint(
            "bundle_id", "event_candidate_evidence_id", name="uq_event_bundle_item"
        ),
        sa.CheckConstraint("packet_digest ~ '^[0-9a-f]{64}$'", name="ck_event_bundle_item_packet"),
        sa.CheckConstraint(
            "projection_hash ~ '^[0-9a-f]{64}$'", name="ck_event_bundle_item_projection"
        ),
        sa.CheckConstraint(
            "fact_identity_digest ~ '^[0-9a-f]{64}$'", name="ck_event_bundle_item_fact_identity"
        ),
        sa.CheckConstraint(
            "fact_value_digest ~ '^[0-9a-f]{64}$'", name="ck_event_bundle_item_fact_value"
        ),
        sa.CheckConstraint("rule_version > 0", name="ck_event_bundle_item_rule_version"),
        sa.CheckConstraint(
            "relation_rule='m2c_relation_v1' AND rule_version=1",
            name="ck_event_bundle_item_rule_exact",
        ),
        sa.CheckConstraint(
            "relation='superseding' OR "
            "(relation='duplicate' AND value_duplicate) OR "
            "(relation='contradicting' AND NOT value_duplicate AND identity_conflict) OR "
            "(relation='supporting' AND NOT value_duplicate AND NOT identity_conflict)",
            name="ck_event_bundle_item_relation_dimensions",
        ),
    )
    op.create_index(
        "ix_event_bundle_item_evidence", "event_evidence_bundle_items", ["evidence_item_id"]
    )
    op.create_table(
        "event_evidence_bundle_heads",
        sa.Column(
            "event_candidate_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("event_candidates.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column(
            "current_bundle_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("event_evidence_bundles.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "canonical_bundle_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("event_evidence_bundles.id", ondelete="RESTRICT"),
            nullable=True,
            unique=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_table(
        "event_evidence_bundle_jobs",
        sa.Column(
            "event_candidate_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("event_candidates.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("status", job_status, nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("safe_error_code", sa.String(100), nullable=True),
        sa.Column("dependency_fingerprint", sa.CHAR(64), nullable=True),
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claim_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "latest_bundle_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("event_evidence_bundles.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_event_bundle_job_attempt_nonnegative"),
        sa.CheckConstraint(
            "(status='processing' AND processing_started_at IS NOT NULL "
            "AND claim_token IS NOT NULL) OR (status<>'processing' "
            "AND processing_started_at IS NULL AND claim_token IS NULL)",
            name="ck_event_bundle_job_processing_state",
        ),
        sa.CheckConstraint(
            "(status='retry' AND safe_error_code IS NOT NULL AND next_retry_at IS NOT NULL) OR "
            "(status<>'retry' AND next_retry_at IS NULL)",
            name="ck_event_bundle_job_retry_state",
        ),
        sa.CheckConstraint(
            "status<>'blocked' OR safe_error_code IS NOT NULL",
            name="ck_event_bundle_job_blocked_error",
        ),
        sa.CheckConstraint(
            "status NOT IN ('pending','processing','ready','partial') OR safe_error_code IS NULL",
            name="ck_event_bundle_job_success_error_empty",
        ),
        sa.CheckConstraint(
            "dependency_fingerprint IS NULL OR dependency_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_event_bundle_job_dependency_fingerprint",
        ),
    )
    op.create_index(
        "ix_event_bundle_job_due",
        "event_evidence_bundle_jobs",
        ["status", "next_retry_at", "updated_at"],
    )

    op.execute("""
    CREATE FUNCTION m2c_bundle_immutable() RETURNS trigger AS $$ BEGIN
      RAISE EXCEPTION 'event_evidence_bundle_immutable';
    END $$ LANGUAGE plpgsql
    """)
    op.execute("""CREATE TRIGGER trg_m2c_bundle_immutable BEFORE UPDATE OR DELETE
      ON event_evidence_bundles FOR EACH ROW EXECUTE FUNCTION m2c_bundle_immutable()""")
    op.execute("""CREATE TRIGGER trg_m2c_bundle_item_immutable BEFORE UPDATE OR DELETE
      ON event_evidence_bundle_items FOR EACH ROW EXECUTE FUNCTION m2c_bundle_immutable()""")
    op.execute("""
    CREATE FUNCTION m2c_bundle_item_guard() RETURNS trigger AS $$
    DECLARE b record; a record; e record; p record;
    BEGIN
      SELECT * INTO b FROM event_evidence_bundles WHERE id=NEW.bundle_id;
      SELECT * INTO a FROM event_candidate_evidence WHERE id=NEW.event_candidate_evidence_id;
      SELECT * INTO e FROM evidence_items WHERE id=NEW.evidence_item_id;
      SELECT p0.* INTO p FROM evidence_projection_links l
        JOIN safe_fact_projections p0 ON p0.id=l.safe_fact_projection_id
        WHERE l.evidence_item_id=NEW.evidence_item_id AND l.status='linked'
          AND p0.projection_hash=NEW.projection_hash
        ORDER BY p0.created_at DESC,p0.id DESC LIMIT 1;
      IF b.id IS NULL OR a.id IS NULL OR e.id IS NULL OR NOT a.active
         OR p.id IS NULL
         OR a.event_candidate_id IS DISTINCT FROM b.event_candidate_id
         OR a.evidence_item_id IS DISTINCT FROM NEW.evidence_item_id
         OR e.provider IS DISTINCT FROM NEW.provider
         OR e.source_id IS DISTINCT FROM NEW.source_id
         OR p.provider IS DISTINCT FROM NEW.provider
         OR p.operation_key IS DISTINCT FROM NEW.operation_key
         OR p.raw_item_id IS DISTINCT FROM e.raw_item_id
         OR NEW.relation_rule IS DISTINCT FROM 'm2c_relation_v1'
         OR NEW.rule_version IS DISTINCT FROM 1 THEN
        RAISE EXCEPTION 'event_evidence_bundle_item_provenance_invalid';
      END IF;
      RETURN NEW;
    END $$ LANGUAGE plpgsql
    """)
    op.execute("""CREATE TRIGGER trg_m2c_bundle_item_guard BEFORE INSERT
      ON event_evidence_bundle_items FOR EACH ROW EXECUTE FUNCTION m2c_bundle_item_guard()""")
    op.execute("""
    CREATE FUNCTION m2c_bundle_head_guard() RETURNS trigger AS $$
    DECLARE cur record; can record; expected_current uuid; expected_canonical uuid;
    BEGIN
      IF TG_OP='DELETE' THEN
        IF EXISTS (
          SELECT 1 FROM event_candidate_evidence
          WHERE event_candidate_id=OLD.event_candidate_id AND active
        ) THEN
          RAISE EXCEPTION 'event_evidence_bundle_head_delete_active';
        END IF;
        RETURN OLD;
      END IF;
      SELECT * INTO cur FROM event_evidence_bundles WHERE id=NEW.current_bundle_id;
      IF cur.event_candidate_id IS DISTINCT FROM NEW.event_candidate_id THEN
        RAISE EXCEPTION 'event_evidence_bundle_head_provenance_invalid';
      END IF;
      SELECT id INTO expected_current FROM event_evidence_bundles
        WHERE event_candidate_id=NEW.event_candidate_id
        ORDER BY revision DESC LIMIT 1;
      SELECT id INTO expected_canonical FROM event_evidence_bundles
        WHERE event_candidate_id=NEW.event_candidate_id AND status='ready'
        ORDER BY revision DESC LIMIT 1;
      IF NEW.current_bundle_id IS DISTINCT FROM expected_current
         OR NEW.canonical_bundle_id IS DISTINCT FROM expected_canonical THEN
        RAISE EXCEPTION 'event_evidence_bundle_head_not_latest';
      END IF;
      IF NEW.canonical_bundle_id IS NOT NULL THEN
        SELECT * INTO can FROM event_evidence_bundles WHERE id=NEW.canonical_bundle_id;
        IF can.event_candidate_id IS DISTINCT FROM NEW.event_candidate_id
           OR can.status <> 'ready' THEN
          RAISE EXCEPTION 'event_evidence_bundle_canonical_invalid';
        END IF;
      END IF;
      RETURN NEW;
    END $$ LANGUAGE plpgsql
    """)
    op.execute("""CREATE TRIGGER trg_m2c_bundle_head_guard BEFORE INSERT OR UPDATE
      ON event_evidence_bundle_heads FOR EACH ROW EXECUTE FUNCTION m2c_bundle_head_guard()""")
    op.execute("""
    CREATE FUNCTION m2c_bundle_job_guard() RETURNS trigger AS $$
    DECLARE latest record;
    BEGIN
      IF NEW.latest_bundle_id IS NOT NULL THEN
        SELECT * INTO latest FROM event_evidence_bundles WHERE id=NEW.latest_bundle_id;
        IF latest.id IS NULL
           OR latest.event_candidate_id IS DISTINCT FROM NEW.event_candidate_id
           OR (NEW.status='ready' AND latest.status<>'ready')
           OR (NEW.status='partial' AND latest.status<>'partial') THEN
          RAISE EXCEPTION 'event_evidence_bundle_job_provenance_invalid';
        END IF;
      ELSIF NEW.status IN ('ready','partial') THEN
        RAISE EXCEPTION 'event_evidence_bundle_job_provenance_invalid';
      END IF;
      RETURN NEW;
    END $$ LANGUAGE plpgsql
    """)
    op.execute("""CREATE TRIGGER trg_m2c_bundle_job_guard BEFORE INSERT OR UPDATE
      ON event_evidence_bundle_jobs FOR EACH ROW EXECUTE FUNCTION m2c_bundle_job_guard()""")
    op.execute("""
    CREATE FUNCTION m2c_bundle_dependency_invalidate() RETURNS trigger AS $$ BEGIN
      IF NEW.status='linked' AND TG_OP='INSERT' THEN
        UPDATE event_evidence_bundle_jobs j SET
          status='pending', attempt_count=0, next_retry_at=NULL, safe_error_code=NULL,
          processing_started_at=NULL, claim_token=NULL, dependency_fingerprint=NULL,
          updated_at=CURRENT_TIMESTAMP
        FROM event_candidate_evidence a
        WHERE a.evidence_item_id=NEW.evidence_item_id AND a.active
          AND j.event_candidate_id=a.event_candidate_id;
      ELSIF NEW.status='linked' AND OLD.status IS DISTINCT FROM NEW.status THEN
        UPDATE event_evidence_bundle_jobs j SET
          status='pending', attempt_count=0, next_retry_at=NULL, safe_error_code=NULL,
          processing_started_at=NULL, claim_token=NULL, dependency_fingerprint=NULL,
          updated_at=CURRENT_TIMESTAMP
        FROM event_candidate_evidence a
        WHERE a.evidence_item_id=NEW.evidence_item_id AND a.active
          AND j.event_candidate_id=a.event_candidate_id;
      END IF;
      RETURN NEW;
    END $$ LANGUAGE plpgsql
    """)
    op.execute("""CREATE TRIGGER trg_m2c_bundle_dependency_invalidate
      AFTER INSERT OR UPDATE OF status ON evidence_projection_links
      FOR EACH ROW EXECUTE FUNCTION m2c_bundle_dependency_invalidate()""")
    op.execute("""
    CREATE FUNCTION m2c_bundle_aggregate_guard() RETURNS trigger AS $$
    DECLARE bundle_identity uuid;
    DECLARE b record;
    DECLARE actual_evidence integer;
    DECLARE actual_sources integer;
    DECLARE actual_providers jsonb;
    DECLARE actual_operations jsonb;
    DECLARE actual_first timestamptz;
    DECLARE actual_last timestamptz;
    DECLARE head_current uuid;
    DECLARE head_canonical uuid;
    DECLARE expected_current uuid;
    DECLARE expected_canonical uuid;
    BEGIN
      IF TG_TABLE_NAME='event_evidence_bundles' THEN
        bundle_identity := NEW.id;
      ELSE
        bundle_identity := NEW.bundle_id;
      END IF;
      SELECT * INTO b FROM event_evidence_bundles WHERE id=bundle_identity;
      SELECT count(*),count(DISTINCT source_id),min(event_time),max(event_time)
        INTO actual_evidence,actual_sources,actual_first,actual_last
        FROM event_evidence_bundle_items WHERE bundle_id=bundle_identity;
      SELECT coalesce(jsonb_agg(provider ORDER BY provider),'[]'::jsonb) INTO actual_providers
        FROM (SELECT DISTINCT provider FROM event_evidence_bundle_items
              WHERE bundle_id=bundle_identity) providers;
      SELECT coalesce(jsonb_agg(operation ORDER BY operation),'[]'::jsonb)
        INTO actual_operations FROM (
          SELECT DISTINCT provider || ':' || operation_key AS operation
          FROM event_evidence_bundle_items WHERE bundle_id=bundle_identity
        ) operations;
      IF EXISTS (
        SELECT 1 FROM event_evidence_bundle_items i
        WHERE i.bundle_id=bundle_identity AND (
          i.value_duplicate IS DISTINCT FROM (
            SELECT count(*) > 1 FROM event_evidence_bundle_items peer
            WHERE peer.bundle_id=i.bundle_id
              AND peer.fact_identity_digest=i.fact_identity_digest
              AND peer.fact_value_digest=i.fact_value_digest
          ) OR i.identity_conflict IS DISTINCT FROM (
            SELECT count(DISTINCT peer.fact_value_digest) > 1
            FROM event_evidence_bundle_items peer
            WHERE peer.bundle_id=i.bundle_id
              AND peer.fact_identity_digest=i.fact_identity_digest
          )
        )
      ) THEN
        RAISE EXCEPTION 'event_evidence_bundle_relation_dimensions_invalid';
      END IF;
      SELECT current_bundle_id,canonical_bundle_id INTO head_current,head_canonical
        FROM event_evidence_bundle_heads WHERE event_candidate_id=b.event_candidate_id;
      SELECT id INTO expected_current FROM event_evidence_bundles
        WHERE event_candidate_id=b.event_candidate_id ORDER BY revision DESC LIMIT 1;
      SELECT id INTO expected_canonical FROM event_evidence_bundles
        WHERE event_candidate_id=b.event_candidate_id AND status='ready'
        ORDER BY revision DESC LIMIT 1;
      IF b.id IS NULL OR b.evidence_count IS DISTINCT FROM actual_evidence
         OR b.source_count IS DISTINCT FROM actual_sources
         OR b.provider_count IS DISTINCT FROM jsonb_array_length(actual_providers)
         OR b.operation_count IS DISTINCT FROM jsonb_array_length(actual_operations)
         OR b.provider_coverage IS DISTINCT FROM actual_providers
         OR b.operation_coverage IS DISTINCT FROM actual_operations
         OR b.first_event_time IS DISTINCT FROM actual_first
         OR b.last_event_time IS DISTINCT FROM actual_last
         OR head_current IS DISTINCT FROM expected_current
         OR head_canonical IS DISTINCT FROM expected_canonical THEN
        RAISE EXCEPTION 'event_evidence_bundle_aggregate_invalid';
      END IF;
      RETURN NEW;
    END $$ LANGUAGE plpgsql
    """)
    op.execute("""CREATE CONSTRAINT TRIGGER trg_m2c_bundle_aggregate_bundle
      AFTER INSERT ON event_evidence_bundles DEFERRABLE INITIALLY DEFERRED
      FOR EACH ROW EXECUTE FUNCTION m2c_bundle_aggregate_guard()""")
    op.execute("""CREATE CONSTRAINT TRIGGER trg_m2c_bundle_aggregate_item
      AFTER INSERT ON event_evidence_bundle_items DEFERRABLE INITIALLY DEFERRED
      FOR EACH ROW EXECUTE FUNCTION m2c_bundle_aggregate_guard()""")


def downgrade() -> None:
    bind = op.get_bind()
    populated = bind.execute(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM event_evidence_bundles) OR "
            "EXISTS(SELECT 1 FROM event_evidence_bundle_jobs)"
        )
    ).scalar_one()
    if populated:
        raise RuntimeError("migration_0011_downgrade_nonempty")
    bind.execute(
        sa.text(
            "DELETE FROM system_metadata WHERE key IN "
            "('event_evidence_bundle_discovery_cursor',"
            "'event_evidence_bundle_dependency_cursor')"
        )
    )
    op.execute("DROP TRIGGER trg_m2c_bundle_aggregate_item ON event_evidence_bundle_items")
    op.execute("DROP TRIGGER trg_m2c_bundle_aggregate_bundle ON event_evidence_bundles")
    op.execute("DROP FUNCTION m2c_bundle_aggregate_guard()")
    op.execute("DROP TRIGGER trg_m2c_bundle_job_guard ON event_evidence_bundle_jobs")
    op.execute("DROP FUNCTION m2c_bundle_job_guard()")
    op.execute("DROP TRIGGER trg_m2c_bundle_dependency_invalidate ON evidence_projection_links")
    op.execute("DROP FUNCTION m2c_bundle_dependency_invalidate()")
    op.execute("DROP TRIGGER trg_m2c_bundle_head_guard ON event_evidence_bundle_heads")
    op.execute("DROP FUNCTION m2c_bundle_head_guard()")
    op.execute("DROP TRIGGER trg_m2c_bundle_item_guard ON event_evidence_bundle_items")
    op.execute("DROP FUNCTION m2c_bundle_item_guard()")
    op.execute("DROP TRIGGER trg_m2c_bundle_item_immutable ON event_evidence_bundle_items")
    op.execute("DROP TRIGGER trg_m2c_bundle_immutable ON event_evidence_bundles")
    op.execute("DROP FUNCTION m2c_bundle_immutable()")
    op.drop_table("event_evidence_bundle_jobs")
    op.drop_table("event_evidence_bundle_heads")
    op.drop_table("event_evidence_bundle_items")
    op.drop_table("event_evidence_bundles")
    op.execute("DROP TRIGGER trg_m2c_event_membership_budget ON event_candidate_evidence")
    op.execute("DROP FUNCTION m2c_event_membership_budget_guard()")
    op.drop_index(
        "uq_event_candidate_evidence_active_owner",
        table_name="event_candidate_evidence",
    )
    postgresql.ENUM(name="event_evidence_bundle_job_status").drop(bind, checkfirst=True)
    postgresql.ENUM(name="event_evidence_relation").drop(bind, checkfirst=True)
    postgresql.ENUM(name="event_evidence_bundle_status").drop(bind, checkfirst=True)
