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
        IF NEW.raw_item_id IS DISTINCT FROM OLD.raw_item_id
          OR NEW.observation_id IS DISTINCT FROM OLD.observation_id
          OR NEW.provider IS DISTINCT FROM OLD.provider
          OR NEW.operation_key IS DISTINCT FROM OLD.operation_key
          OR NEW.projection_schema_version IS DISTINCT FROM OLD.projection_schema_version
          OR NEW.factual_payload IS DISTINCT FROM OLD.factual_payload
          OR NEW.projection_hash IS DISTINCT FROM OLD.projection_hash
          OR NEW.quality_status IS DISTINCT FROM OLD.quality_status
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
