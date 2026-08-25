"""Create durable R8-A factual projection to evidence handoff links."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    postgresql.ENUM(
        "pending",
        "processing",
        "linked",
        "retry",
        "blocked",
        name="evidence_projection_link_status",
    ).create(op.get_bind(), checkfirst=True)
    op.create_table(
        "evidence_projection_links",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "safe_fact_projection_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("safe_fact_projections.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "evidence_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("evidence_items.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "content_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("content_items.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "status",
            postgresql.ENUM(name="evidence_projection_link_status", create_type=False),
            nullable=False,
        ),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("safe_error_code", sa.String(100), nullable=True),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=True),
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
            "safe_fact_projection_id", name="uq_evidence_projection_links_projection"
        ),
        sa.CheckConstraint(
            "attempt_count >= 0", name="ck_evidence_projection_links_attempt_nonnegative"
        ),
        sa.CheckConstraint(
            "(status = 'linked' AND evidence_item_id IS NOT NULL AND linked_at IS NOT NULL) OR "
            "(status <> 'linked' AND evidence_item_id IS NULL "
            "AND content_item_id IS NULL AND linked_at IS NULL)",
            name="ck_evidence_projection_links_linked_state",
        ),
    )
    op.create_index(
        "ix_evidence_projection_links_claim",
        "evidence_projection_links",
        ["status", "next_retry_at", "created_at"],
    )
    op.create_index(
        "ix_evidence_projection_links_evidence", "evidence_projection_links", ["evidence_item_id"]
    )
    op.create_index(
        "ix_evidence_projection_links_content", "evidence_projection_links", ["content_item_id"]
    )

    op.execute("""
    CREATE FUNCTION r8a_evidence_projection_link_guard() RETURNS trigger AS $$
    DECLARE p safe_fact_projections%ROWTYPE;
            r raw_items%ROWTYPE;
            e evidence_items%ROWTYPE;
            c content_items%ROWTYPE;
    BEGIN
      SELECT * INTO p FROM safe_fact_projections WHERE id=NEW.safe_fact_projection_id;
      IF NOT FOUND OR p.processing_status <> 'ready' THEN
        RAISE EXCEPTION 'evidence_projection_not_ready';
      END IF;
      SELECT * INTO r FROM raw_items WHERE id=p.raw_item_id;
      IF NEW.evidence_item_id IS NOT NULL THEN
        SELECT * INTO e FROM evidence_items WHERE id=NEW.evidence_item_id;
        IF NOT FOUND OR e.raw_item_id IS DISTINCT FROM p.raw_item_id
           OR e.source_id IS DISTINCT FROM r.source_id
           OR e.source_account_id IS DISTINCT FROM r.source_account_id
           OR e.provider IS DISTINCT FROM p.provider
           OR (e.content_item_id IS NOT NULL
               AND e.content_item_id IS DISTINCT FROM NEW.content_item_id) THEN
          RAISE EXCEPTION 'evidence_projection_evidence_provenance_mismatch';
        END IF;
      END IF;
      IF NEW.content_item_id IS NOT NULL THEN
        SELECT * INTO c FROM content_items WHERE id=NEW.content_item_id;
        IF NOT FOUND OR c.raw_item_id IS DISTINCT FROM p.raw_item_id
           OR c.source_id IS DISTINCT FROM r.source_id
           OR c.source_account_id IS DISTINCT FROM r.source_account_id THEN
          RAISE EXCEPTION 'evidence_projection_content_provenance_mismatch';
        END IF;
      END IF;
      IF p.provider IN ('finnhub','eia')
         AND (NEW.content_item_id IS NOT NULL OR e.content_item_id IS NOT NULL) THEN
        RAISE EXCEPTION 'evidence_projection_content_forbidden';
      END IF;
      IF p.provider='marketaux' AND NEW.content_item_id IS NOT NULL
         AND c.content_kind <> 'article' THEN
        RAISE EXCEPTION 'evidence_projection_content_kind_mismatch';
      END IF;
      IF p.provider='sec_edgar' AND NEW.content_item_id IS NOT NULL THEN
        IF c.content_kind <> 'official_release' OR c.body_availability <> 'unavailable'
           OR c.canonical_url IS NULL OR c.original_url IS DISTINCT FROM c.canonical_url
           OR NOT (
             c.canonical_url = p.factual_payload->>'official_url'
             OR EXISTS (
               SELECT 1 FROM evidence_projection_links prior
               JOIN safe_fact_projections prior_p ON prior_p.id=prior.safe_fact_projection_id
               WHERE prior.status='linked' AND prior.evidence_item_id=NEW.evidence_item_id
                 AND prior.content_item_id=NEW.content_item_id
                 AND prior_p.factual_payload->>'official_url'=c.canonical_url
             )
           ) THEN
          RAISE EXCEPTION 'evidence_projection_sec_content_invalid';
        END IF;
      END IF;
      IF (p.provider='marketaux' AND e.provider_item_type <> 'marketaux_news')
         OR (p.provider='finnhub' AND e.provider_item_type <> 'finnhub_quote')
         OR (p.provider='eia' AND e.provider_item_type <> 'eia_energy_timeseries')
         OR (p.provider='sec_edgar' AND e.provider_item_type <> 'sec_filing') THEN
        RAISE EXCEPTION 'evidence_projection_evidence_type_mismatch';
      END IF;
      IF NEW.evidence_item_id IS NOT NULL AND e.content_item_id IS NOT NULL
         AND NEW.content_item_id IS NULL THEN
        RAISE EXCEPTION 'evidence_projection_content_provenance_mismatch';
      END IF;
      IF TG_OP='UPDATE' AND OLD.status='linked'
         AND (NEW.safe_fact_projection_id IS DISTINCT FROM OLD.safe_fact_projection_id
           OR NEW.evidence_item_id IS DISTINCT FROM OLD.evidence_item_id
           OR NEW.content_item_id IS DISTINCT FROM OLD.content_item_id
           OR NEW.status IS DISTINCT FROM OLD.status
           OR NEW.linked_at IS DISTINCT FROM OLD.linked_at) THEN
        RAISE EXCEPTION 'evidence_projection_link_immutable';
      END IF;
      RETURN NEW;
    END; $$ LANGUAGE plpgsql
    """)
    op.execute("""
    CREATE TRIGGER trg_r8a_evidence_projection_link_guard BEFORE INSERT OR UPDATE
      ON evidence_projection_links FOR EACH ROW
      EXECUTE FUNCTION r8a_evidence_projection_link_guard()
    """)
    op.execute("""
    CREATE FUNCTION r8a_evidence_content_policy_guard() RETURNS trigger AS $$
    BEGIN
      IF NEW.provider IN ('finnhub','eia') AND NEW.content_item_id IS NOT NULL THEN
        RAISE EXCEPTION 'evidence_projection_content_forbidden';
      END IF;
      RETURN NEW;
    END; $$ LANGUAGE plpgsql
    """)
    op.execute("""
    CREATE TRIGGER trg_r8a_evidence_content_policy_guard BEFORE INSERT OR UPDATE
      ON evidence_items FOR EACH ROW EXECUTE FUNCTION r8a_evidence_content_policy_guard()
    """)
    op.execute("""
    CREATE FUNCTION r8a_linked_evidence_immutable_guard() RETURNS trigger AS $$
    BEGIN
      IF EXISTS (SELECT 1 FROM evidence_projection_links WHERE evidence_item_id=OLD.id)
         AND (NEW.raw_item_id IS DISTINCT FROM OLD.raw_item_id
           OR NEW.source_id IS DISTINCT FROM OLD.source_id
           OR NEW.source_account_id IS DISTINCT FROM OLD.source_account_id
           OR NEW.provider IS DISTINCT FROM OLD.provider
           OR NEW.provider_item_id IS DISTINCT FROM OLD.provider_item_id
           OR NEW.provider_item_hash IS DISTINCT FROM OLD.provider_item_hash) THEN
        RAISE EXCEPTION 'linked_evidence_identity_immutable';
      END IF;
      RETURN NEW;
    END; $$ LANGUAGE plpgsql
    """)
    op.execute("""
    CREATE TRIGGER trg_r8a_linked_evidence_immutable_guard BEFORE UPDATE ON evidence_items
      FOR EACH ROW EXECUTE FUNCTION r8a_linked_evidence_immutable_guard()
    """)
    op.execute("""
    CREATE FUNCTION r8a_linked_content_immutable_guard() RETURNS trigger AS $$
    BEGIN
      IF EXISTS (SELECT 1 FROM evidence_projection_links WHERE content_item_id=OLD.id)
         AND (NEW.raw_item_id IS DISTINCT FROM OLD.raw_item_id
           OR NEW.source_id IS DISTINCT FROM OLD.source_id
           OR NEW.source_account_id IS DISTINCT FROM OLD.source_account_id
           OR NEW.content_kind IS DISTINCT FROM OLD.content_kind
           OR NEW.body_availability IS DISTINCT FROM OLD.body_availability
           OR NEW.original_url IS DISTINCT FROM OLD.original_url
           OR NEW.canonical_url IS DISTINCT FROM OLD.canonical_url) THEN
        RAISE EXCEPTION 'linked_content_provenance_immutable';
      END IF;
      RETURN NEW;
    END; $$ LANGUAGE plpgsql
    """)
    op.execute("""
    CREATE TRIGGER trg_r8a_linked_content_immutable_guard BEFORE UPDATE ON content_items
      FOR EACH ROW EXECUTE FUNCTION r8a_linked_content_immutable_guard()
    """)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT count(*) FROM evidence_projection_links")).scalar_one():
        raise RuntimeError("migration_0008_downgrade_requires_empty_handoff_state")
    op.execute("DROP TRIGGER trg_r8a_linked_content_immutable_guard ON content_items")
    op.execute("DROP FUNCTION r8a_linked_content_immutable_guard()")
    op.execute("DROP TRIGGER trg_r8a_linked_evidence_immutable_guard ON evidence_items")
    op.execute("DROP FUNCTION r8a_linked_evidence_immutable_guard()")
    op.execute("DROP TRIGGER IF EXISTS trg_r8a_evidence_content_policy_guard ON evidence_items")
    op.execute("DROP FUNCTION IF EXISTS r8a_evidence_content_policy_guard()")
    op.execute("DROP TRIGGER trg_r8a_evidence_projection_link_guard ON evidence_projection_links")
    op.execute("DROP FUNCTION r8a_evidence_projection_link_guard()")
    op.drop_table("evidence_projection_links")
    postgresql.ENUM(name="evidence_projection_link_status").drop(bind, checkfirst=True)
