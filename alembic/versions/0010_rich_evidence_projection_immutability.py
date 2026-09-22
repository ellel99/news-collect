"""Protect factual projection fields after durable evidence linking."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "evidence_projection_links",
        sa.Column("canonical_evidence", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "evidence_projection_links",
        sa.Column("canonical_content", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # Existing 0008 state is accepted only when the canonical Evidence and Content
    # origins are uniquely recoverable from their immutable factual timestamps/fields.
    op.execute("""
    DO $$ BEGIN
      IF EXISTS (
        SELECT 1 FROM evidence_projection_links l
        LEFT JOIN safe_fact_projections p ON p.id=l.safe_fact_projection_id
        LEFT JOIN raw_item_observations o ON o.id=p.observation_id
        LEFT JOIN raw_items r ON r.id=p.raw_item_id
        LEFT JOIN evidence_items e ON e.id=l.evidence_item_id
        LEFT JOIN sources s ON s.id=r.source_id
        WHERE l.status='linked' AND (
          p.id IS NULL OR o.id IS NULL OR r.id IS NULL OR e.id IS NULL OR s.id IS NULL
          OR p.processing_status <> 'ready' OR p.safe_error_code IS NOT NULL
          OR p.next_retry_at IS NOT NULL OR p.processed_at IS NULL
          OR p.raw_item_id IS DISTINCT FROM r.id
          OR o.raw_item_id IS DISTINCT FROM r.id
          OR o.provider IS DISTINCT FROM p.provider
          OR o.operation_key IS DISTINCT FROM p.operation_key
          OR o.projection_hash IS DISTINCT FROM p.projection_hash
          OR e.raw_item_id IS DISTINCT FROM r.id OR e.provider IS DISTINCT FROM p.provider
          OR e.source_id IS DISTINCT FROM r.source_id
          OR e.source_account_id IS DISTINCT FROM r.source_account_id
          OR r.retention_class IS DISTINCT FROM s.retention_class
          OR NOT (
            (p.provider='marketaux' AND p.operation_key='news_all'
              AND e.provider_item_type='marketaux_news' AND e.evidence_kind='news'
              AND e.source_type='news' AND e.access_level='link_only')
            OR (p.provider='finnhub' AND p.operation_key='quote'
              AND e.provider_item_type='finnhub_quote' AND e.evidence_kind='market_data'
              AND e.source_type='market_data' AND e.access_level='licensed')
            OR (p.provider='finnhub' AND p.operation_key='company_news'
              AND e.provider_item_type='finnhub_company_news' AND e.evidence_kind='news'
              AND e.source_type='news' AND e.access_level='licensed')
            OR (p.provider='eia' AND p.operation_key IN
                  ('electricity_retail_sales','electricity_rto_region_data')
              AND e.provider_item_type='eia_energy_timeseries'
              AND e.evidence_kind='energy_official' AND e.source_type='official_energy'
              AND e.access_level='public_summary')
            OR (p.provider='sec_edgar' AND p.operation_key='submissions_recent'
              AND e.provider_item_type='sec_filing' AND e.evidence_kind='disclosure'
              AND e.source_type='disclosure' AND e.access_level='link_only')
          )
        )
      ) THEN RAISE EXCEPTION 'migration_0010_existing_linked_lineage_invalid'; END IF;
      IF EXISTS (
        SELECT 1 FROM evidence_projection_links l
        JOIN safe_fact_projections p ON p.id=l.safe_fact_projection_id
        JOIN evidence_items e ON e.id=l.evidence_item_id
        LEFT JOIN content_items c ON c.id=l.content_item_id
        WHERE l.status='linked' AND (
          ((p.provider='eia' OR (p.provider='finnhub' AND p.operation_key='quote'))
            AND l.content_item_id IS NOT NULL)
          OR (l.content_item_id IS NOT NULL AND (
            c.id IS NULL OR c.raw_item_id IS DISTINCT FROM p.raw_item_id
            OR c.body IS NOT NULL OR c.source_summary IS NOT NULL OR c.author IS NOT NULL
            OR c.content_hash IS NOT NULL OR c.source_updated_at IS NOT NULL
            OR c.reply_to_external_id IS NOT NULL OR c.quote_external_id IS NOT NULL
            OR c.repost_external_id IS NOT NULL OR c.deleted_status <> 'unknown'
            OR c.metadata - ARRAY['provider','operation_key','retention'] <> '{}'::jsonb
            OR (p.provider='marketaux' AND c.language IS DISTINCT FROM
                p.factual_payload->>'language')
            OR (p.provider IN ('finnhub','sec_edgar') AND c.language IS NOT NULL)
            OR (p.provider IN ('marketaux','finnhub') AND c.content_kind <> 'article')
            OR (p.provider='sec_edgar' AND (c.content_kind <> 'official_release'
                OR c.body_availability <> 'unavailable'))
          ))
        )
      ) THEN RAISE EXCEPTION 'migration_0010_existing_linked_content_invalid'; END IF;
    END $$
    """)
    op.execute("""
    DO $$ BEGIN
      IF EXISTS (
        WITH candidates AS (
          SELECT l.id, l.evidence_item_id
          FROM evidence_projection_links l
          JOIN safe_fact_projections p ON p.id=l.safe_fact_projection_id
          JOIN raw_item_observations o ON o.id=p.observation_id
          JOIN evidence_items e ON e.id=l.evidence_item_id
          WHERE l.status='linked'
            AND e.event_time=CASE
              WHEN p.factual_payload->>'published_at' ~
                '^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}([.]\\d+)?([+]\\d{2}:\\d{2}|Z)$'
              THEN (p.factual_payload->>'published_at')::timestamptz ELSE NULL END
            AND e.observed_at=o.observed_at
        )
        SELECT 1 FROM evidence_items e
        JOIN evidence_projection_links l ON l.evidence_item_id=e.id AND l.status='linked'
        LEFT JOIN candidates c ON c.evidence_item_id=e.id
        GROUP BY e.id HAVING count(DISTINCT c.id) <> 1
      ) THEN RAISE EXCEPTION 'migration_0010_canonical_evidence_ambiguous'; END IF;
    END $$
    """)
    op.execute("""
    UPDATE evidence_projection_links l SET canonical_evidence=true
    FROM safe_fact_projections p, raw_item_observations o, evidence_items e
    WHERE l.safe_fact_projection_id=p.id AND o.id=p.observation_id
      AND e.id=l.evidence_item_id AND l.status='linked'
      AND e.event_time=CASE
        WHEN p.factual_payload->>'published_at' ~
          '^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}([.]\\d+)?([+]\\d{2}:\\d{2}|Z)$'
        THEN (p.factual_payload->>'published_at')::timestamptz ELSE NULL END
      AND e.observed_at=o.observed_at
    """)
    op.execute("""
    DO $$ BEGIN
      IF EXISTS (
        WITH candidates AS (
          SELECT l.id,l.content_item_id FROM evidence_projection_links l
          JOIN safe_fact_projections p ON p.id=l.safe_fact_projection_id
          JOIN content_items c ON c.id=l.content_item_id
          WHERE l.status='linked' AND l.content_item_id IS NOT NULL
            AND c.source_published_at=CASE
              WHEN p.factual_payload->>'published_at' ~
                '^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}([.]\\d+)?([+]\\d{2}:\\d{2}|Z)$'
              THEN (p.factual_payload->>'published_at')::timestamptz ELSE NULL END
            AND c.original_url=COALESCE(p.factual_payload->>'canonical_url',
                                        p.factual_payload->>'official_url')
            AND c.canonical_url=c.original_url
            AND (p.provider='sec_edgar' OR c.title=p.factual_payload->>'title')
        )
        SELECT 1 FROM evidence_projection_links l
        LEFT JOIN candidates c ON c.content_item_id=l.content_item_id
        WHERE l.status='linked' AND l.content_item_id IS NOT NULL
        GROUP BY l.content_item_id HAVING count(DISTINCT c.id) <> 1
      ) THEN RAISE EXCEPTION 'migration_0010_canonical_content_ambiguous'; END IF;
    END $$
    """)
    op.execute("""
    UPDATE evidence_projection_links l SET canonical_content=true
    FROM safe_fact_projections p, content_items c
    WHERE l.safe_fact_projection_id=p.id AND c.id=l.content_item_id AND l.status='linked'
      AND c.source_published_at=CASE
        WHEN p.factual_payload->>'published_at' ~
          '^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}([.]\\d+)?([+]\\d{2}:\\d{2}|Z)$'
        THEN (p.factual_payload->>'published_at')::timestamptz ELSE NULL END
      AND c.original_url=COALESCE(p.factual_payload->>'canonical_url',
                                  p.factual_payload->>'official_url')
      AND c.canonical_url=c.original_url
      AND (p.provider='sec_edgar' OR c.title=p.factual_payload->>'title')
    """)
    op.create_check_constraint(
        "ck_evidence_projection_links_canonical_evidence_linked",
        "evidence_projection_links",
        "NOT canonical_evidence OR status = 'linked'",
    )
    op.create_check_constraint(
        "ck_evidence_projection_links_canonical_content_linked",
        "evidence_projection_links",
        "NOT canonical_content OR (status = 'linked' AND content_item_id IS NOT NULL)",
    )
    op.create_index(
        "uq_evidence_projection_links_canonical_evidence",
        "evidence_projection_links",
        ["evidence_item_id"],
        unique=True,
        postgresql_where=sa.text("canonical_evidence"),
    )
    op.create_index(
        "uq_evidence_projection_links_canonical_content",
        "evidence_projection_links",
        ["content_item_id"],
        unique=True,
        postgresql_where=sa.text("canonical_content"),
    )
    op.execute("""
    CREATE FUNCTION m2b_link_transition_lock_guard() RETURNS trigger AS $$
    DECLARE raw_identity uuid; source_identity uuid; provider_key text;
            operation_identity text; c content_items%ROWTYPE;
    BEGIN
      IF NEW.status='linked' AND (TG_OP='INSERT' OR OLD.status IS DISTINCT FROM 'linked') THEN
        SELECT p.raw_item_id,r.source_id,p.provider,p.operation_key
          INTO raw_identity,source_identity,provider_key,operation_identity
        FROM safe_fact_projections p JOIN raw_items r ON r.id=p.raw_item_id
        WHERE p.id=NEW.safe_fact_projection_id;
        PERFORM pg_advisory_xact_lock(hashtextextended('source:'||source_identity::text,0));
        PERFORM pg_advisory_xact_lock(hashtextextended('raw:'||raw_identity::text,0));
        IF NOT EXISTS (
          SELECT 1 FROM safe_fact_projections p
          JOIN raw_items r ON r.id=p.raw_item_id
          JOIN sources s ON s.id=r.source_id
          JOIN evidence_items e ON e.id=NEW.evidence_item_id
          WHERE p.id=NEW.safe_fact_projection_id
            AND s.retention_class=r.retention_class
            AND e.raw_item_id=r.id AND e.source_id=r.source_id
            AND e.source_account_id IS NOT DISTINCT FROM r.source_account_id
            AND e.provider=p.provider
            AND (
              (p.provider='marketaux' AND p.operation_key='news_all'
                AND e.provider_item_type='marketaux_news' AND e.evidence_kind='news'
                AND e.source_type='news' AND e.access_level='link_only')
              OR (p.provider='finnhub' AND p.operation_key='quote'
                AND e.provider_item_type='finnhub_quote' AND e.evidence_kind='market_data'
                AND e.source_type='market_data' AND e.access_level='licensed')
              OR (p.provider='finnhub' AND p.operation_key='company_news'
                AND e.provider_item_type='finnhub_company_news' AND e.evidence_kind='news'
                AND e.source_type='news' AND e.access_level='licensed')
              OR (p.provider='eia' AND p.operation_key IN
                    ('electricity_retail_sales','electricity_rto_region_data')
                AND e.provider_item_type='eia_energy_timeseries'
                AND e.evidence_kind='energy_official' AND e.source_type='official_energy'
                AND e.access_level='public_summary')
              OR (p.provider='sec_edgar' AND p.operation_key='submissions_recent'
                AND e.provider_item_type='sec_filing' AND e.evidence_kind='disclosure'
                AND e.source_type='disclosure' AND e.access_level='link_only')
            )
        ) THEN RAISE EXCEPTION 'linked_operation_policy_invalid'; END IF;
        IF EXISTS (SELECT 1 FROM evidence_projection_links
                   WHERE evidence_item_id=NEW.evidence_item_id AND status='linked')
             IS DISTINCT FROM (NOT NEW.canonical_evidence) THEN
          RAISE EXCEPTION 'canonical_evidence_adoption_invalid';
        END IF;
        IF NEW.content_item_id IS NULL AND NEW.canonical_content THEN
          RAISE EXCEPTION 'canonical_content_adoption_invalid';
        END IF;
        IF NEW.content_item_id IS NOT NULL THEN
          SELECT * INTO c FROM content_items WHERE id=NEW.content_item_id;
          IF (EXISTS (SELECT 1 FROM evidence_projection_links
                      WHERE content_item_id=NEW.content_item_id AND status='linked'))
               IS DISTINCT FROM (NOT NEW.canonical_content) THEN
            RAISE EXCEPTION 'canonical_content_adoption_invalid';
          END IF;
          IF c.body IS NOT NULL OR c.source_summary IS NOT NULL OR c.author IS NOT NULL
             OR c.content_hash IS NOT NULL OR c.source_updated_at IS NOT NULL
             OR c.reply_to_external_id IS NOT NULL OR c.quote_external_id IS NOT NULL
             OR c.repost_external_id IS NOT NULL OR c.deleted_status <> 'unknown'
             OR c.metadata - ARRAY['provider','operation_key','retention'] <> '{}'::jsonb
             OR c.metadata->>'provider' IS DISTINCT FROM provider_key
             OR c.metadata->>'operation_key' IS DISTINCT FROM operation_identity
             OR (provider_key='marketaux' AND c.language IS DISTINCT FROM
                 (SELECT factual_payload->>'language' FROM safe_fact_projections
                  WHERE id=NEW.safe_fact_projection_id))
             OR (provider_key IN ('finnhub','sec_edgar') AND c.language IS NOT NULL) THEN
            RAISE EXCEPTION 'linked_content_field_policy_invalid';
          END IF;
          IF NEW.canonical_content AND NOT EXISTS (
            SELECT 1 FROM safe_fact_projections p WHERE p.id=NEW.safe_fact_projection_id
              AND c.source_published_at=(p.factual_payload->>'published_at')::timestamptz
              AND c.original_url=COALESCE(p.factual_payload->>'canonical_url',
                                          p.factual_payload->>'official_url')
              AND c.canonical_url=c.original_url
              AND (
                (p.provider IN ('marketaux','finnhub') AND c.content_kind='article'
                  AND c.title=p.factual_payload->>'title')
                OR (p.provider='sec_edgar' AND c.content_kind='official_release')
              )
          ) THEN RAISE EXCEPTION 'canonical_content_projection_mismatch'; END IF;
        END IF;
      END IF;
      RETURN NEW;
    END; $$ LANGUAGE plpgsql
    """)
    op.execute("""
    CREATE TRIGGER trg_m2b_00_link_transition_lock_guard
      BEFORE INSERT OR UPDATE ON evidence_projection_links
      FOR EACH ROW EXECUTE FUNCTION m2b_link_transition_lock_guard()
    """)
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
      PERFORM pg_advisory_xact_lock(hashtextextended('raw:'||OLD.raw_item_id::text,0));
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
      PERFORM pg_advisory_xact_lock(hashtextextended('raw:'||OLD.id::text,0));
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
      PERFORM pg_advisory_xact_lock(hashtextextended('source:'||OLD.id::text,0));
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
      PERFORM pg_advisory_xact_lock(hashtextextended('raw:'||OLD.raw_item_id::text,0));
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
      PERFORM pg_advisory_xact_lock(hashtextextended('raw:'||OLD.raw_item_id::text,0));
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
    op.execute("DROP TRIGGER trg_m2b_00_link_transition_lock_guard ON evidence_projection_links")
    op.execute("DROP FUNCTION m2b_link_transition_lock_guard()")
    op.drop_index(
        "uq_evidence_projection_links_canonical_content",
        table_name="evidence_projection_links",
    )
    op.drop_index(
        "uq_evidence_projection_links_canonical_evidence",
        table_name="evidence_projection_links",
    )
    op.drop_constraint(
        "ck_evidence_projection_links_canonical_content_linked",
        "evidence_projection_links",
        type_="check",
    )
    op.drop_constraint(
        "ck_evidence_projection_links_canonical_evidence_linked",
        "evidence_projection_links",
        type_="check",
    )
    op.drop_column("evidence_projection_links", "canonical_content")
    op.drop_column("evidence_projection_links", "canonical_evidence")
