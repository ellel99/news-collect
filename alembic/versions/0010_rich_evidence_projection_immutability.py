"""Protect factual projection fields after durable evidence linking."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
    CREATE FUNCTION m2b_linked_association_immutable_guard() RETURNS trigger AS $$
    BEGIN
      IF OLD.status='linked' THEN
        IF TG_OP='DELETE' OR
           (to_jsonb(NEW) - ARRAY['updated_at'])
             IS DISTINCT FROM (to_jsonb(OLD) - ARRAY['updated_at']) THEN
          RAISE EXCEPTION 'linked_evidence_projection_association_immutable';
        END IF;
      END IF;
      IF TG_OP='DELETE' THEN RETURN OLD; END IF;
      RETURN NEW;
    END; $$ LANGUAGE plpgsql
    """)
    op.execute("""
    CREATE TRIGGER trg_m2b_linked_association_immutable_guard
      BEFORE UPDATE OR DELETE ON evidence_projection_links
      FOR EACH ROW EXECUTE FUNCTION m2b_linked_association_immutable_guard()
    """)
    op.execute("""
    CREATE FUNCTION m2b_linked_projection_immutable_guard() RETURNS trigger AS $$
    BEGIN
      IF EXISTS (
        SELECT 1 FROM evidence_projection_links
        WHERE safe_fact_projection_id=OLD.id AND status='linked'
      ) THEN
        IF TG_OP='DELETE' THEN
          RAISE EXCEPTION 'linked_safe_fact_projection_immutable';
        END IF;
        IF NEW.processing_status <> 'ready'
          OR NEW.safe_error_code IS NOT NULL
          OR NEW.next_retry_at IS NOT NULL
          OR NEW.processed_at IS NULL
          OR (to_jsonb(NEW) - ARRAY['updated_at'])
             IS DISTINCT FROM (to_jsonb(OLD) - ARRAY['updated_at'])
        THEN
          RAISE EXCEPTION 'linked_safe_fact_projection_immutable';
        END IF;
      END IF;
      IF TG_OP='DELETE' THEN
        RETURN OLD;
      END IF;
      RETURN NEW;
    END; $$ LANGUAGE plpgsql
    """)
    op.execute("""
    CREATE FUNCTION m2b_linked_observation_immutable_guard() RETURNS trigger AS $$
    BEGIN
      IF EXISTS (
        SELECT 1 FROM safe_fact_projections p
        JOIN evidence_projection_links l ON l.safe_fact_projection_id=p.id
        WHERE p.observation_id=OLD.id AND l.status='linked'
      ) THEN
        IF TG_OP='DELETE' OR to_jsonb(NEW) IS DISTINCT FROM to_jsonb(OLD) THEN
          RAISE EXCEPTION 'linked_raw_item_observation_immutable';
        END IF;
      END IF;
      IF TG_OP='DELETE' THEN RETURN OLD; END IF;
      RETURN NEW;
    END; $$ LANGUAGE plpgsql
    """)
    op.execute("""
    CREATE TRIGGER trg_m2b_linked_observation_immutable_guard
      BEFORE UPDATE OR DELETE ON raw_item_observations
      FOR EACH ROW EXECUTE FUNCTION m2b_linked_observation_immutable_guard()
    """)
    op.execute("""
    CREATE FUNCTION m2b_linked_raw_item_immutable_guard() RETURNS trigger AS $$
    BEGIN
      IF EXISTS (
        SELECT 1 FROM safe_fact_projections p
        JOIN evidence_projection_links l ON l.safe_fact_projection_id=p.id
        WHERE p.raw_item_id=OLD.id AND l.status='linked'
      ) THEN
        IF TG_OP='DELETE' OR to_jsonb(NEW) IS DISTINCT FROM to_jsonb(OLD) THEN
          RAISE EXCEPTION 'linked_raw_item_immutable';
        END IF;
      END IF;
      IF TG_OP='DELETE' THEN RETURN OLD; END IF;
      RETURN NEW;
    END; $$ LANGUAGE plpgsql
    """)
    op.execute("""
    CREATE TRIGGER trg_m2b_linked_raw_item_immutable_guard
      BEFORE UPDATE OR DELETE ON raw_items
      FOR EACH ROW EXECUTE FUNCTION m2b_linked_raw_item_immutable_guard()
    """)
    op.execute("""
    CREATE FUNCTION m2b_linked_source_retention_guard() RETURNS trigger AS $$
    BEGIN
      IF NEW.retention_class IS DISTINCT FROM OLD.retention_class AND EXISTS (
        SELECT 1 FROM raw_items r
        JOIN safe_fact_projections p ON p.raw_item_id=r.id
        JOIN evidence_projection_links l ON l.safe_fact_projection_id=p.id
        WHERE r.source_id=OLD.id AND l.status='linked'
      ) THEN
        RAISE EXCEPTION 'linked_source_retention_immutable';
      END IF;
      RETURN NEW;
    END; $$ LANGUAGE plpgsql
    """)
    op.execute("""
    CREATE TRIGGER trg_m2b_linked_source_retention_guard
      BEFORE UPDATE ON sources
      FOR EACH ROW EXECUTE FUNCTION m2b_linked_source_retention_guard()
    """)
    op.execute("""
    CREATE FUNCTION m2b_linked_evidence_row_immutable_guard() RETURNS trigger AS $$
    BEGIN
      IF EXISTS (
        SELECT 1 FROM evidence_projection_links l
        WHERE l.evidence_item_id=OLD.id AND l.status='linked'
      ) AND (TG_OP='DELETE' OR to_jsonb(NEW) IS DISTINCT FROM to_jsonb(OLD)) THEN
        RAISE EXCEPTION 'linked_evidence_row_immutable';
      END IF;
      IF TG_OP='DELETE' THEN RETURN OLD; END IF;
      RETURN NEW;
    END; $$ LANGUAGE plpgsql
    """)
    op.execute("""
    CREATE TRIGGER trg_m2b_linked_evidence_row_immutable_guard
      BEFORE UPDATE OR DELETE ON evidence_items
      FOR EACH ROW EXECUTE FUNCTION m2b_linked_evidence_row_immutable_guard()
    """)
    op.execute("""
    CREATE FUNCTION m2b_linked_content_row_immutable_guard() RETURNS trigger AS $$
    BEGIN
      IF EXISTS (
        SELECT 1 FROM evidence_projection_links l
        WHERE l.content_item_id=OLD.id AND l.status='linked'
      ) AND (TG_OP='DELETE' OR
        (to_jsonb(NEW) - ARRAY['updated_at'])
          IS DISTINCT FROM (to_jsonb(OLD) - ARRAY['updated_at'])) THEN
        RAISE EXCEPTION 'linked_content_row_immutable';
      END IF;
      IF TG_OP='DELETE' THEN RETURN OLD; END IF;
      RETURN NEW;
    END; $$ LANGUAGE plpgsql
    """)
    op.execute("""
    CREATE TRIGGER trg_m2b_linked_content_row_immutable_guard
      BEFORE UPDATE OR DELETE ON content_items
      FOR EACH ROW EXECUTE FUNCTION m2b_linked_content_row_immutable_guard()
    """)
    op.execute("""
    CREATE TRIGGER trg_m2b_linked_projection_immutable_guard
      BEFORE UPDATE OR DELETE ON safe_fact_projections
      FOR EACH ROW EXECUTE FUNCTION m2b_linked_projection_immutable_guard()
    """)


def downgrade() -> None:
    bind = op.get_bind()
    linked = bind.execute(
        sa.text("SELECT count(*) FROM evidence_projection_links WHERE status='linked'")
    ).scalar_one()
    if linked:
        raise RuntimeError("migration_0010_downgrade_requires_no_linked_projection_state")
    op.execute("DROP TRIGGER trg_m2b_linked_content_row_immutable_guard ON content_items")
    op.execute("DROP FUNCTION m2b_linked_content_row_immutable_guard()")
    op.execute("DROP TRIGGER trg_m2b_linked_evidence_row_immutable_guard ON evidence_items")
    op.execute("DROP FUNCTION m2b_linked_evidence_row_immutable_guard()")
    op.execute("DROP TRIGGER trg_m2b_linked_source_retention_guard ON sources")
    op.execute("DROP FUNCTION m2b_linked_source_retention_guard()")
    op.execute("DROP TRIGGER trg_m2b_linked_raw_item_immutable_guard ON raw_items")
    op.execute("DROP FUNCTION m2b_linked_raw_item_immutable_guard()")
    op.execute("DROP TRIGGER trg_m2b_linked_observation_immutable_guard ON raw_item_observations")
    op.execute("DROP FUNCTION m2b_linked_observation_immutable_guard()")
    op.execute("DROP TRIGGER trg_m2b_linked_projection_immutable_guard ON safe_fact_projections")
    op.execute("DROP FUNCTION m2b_linked_projection_immutable_guard()")
    op.execute(
        "DROP TRIGGER trg_m2b_linked_association_immutable_guard ON evidence_projection_links"
    )
    op.execute("DROP FUNCTION m2b_linked_association_immutable_guard()")
