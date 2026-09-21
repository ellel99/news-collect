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
    op.execute("DROP TRIGGER trg_m2b_linked_projection_immutable_guard ON safe_fact_projections")
    op.execute("DROP FUNCTION m2b_linked_projection_immutable_guard()")
