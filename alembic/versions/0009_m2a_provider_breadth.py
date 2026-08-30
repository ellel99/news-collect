"""M2-A forward schema migration; requires an explicit stopped-writer boundary."""
# ruff: noqa: E501

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _checks(expanded: bool) -> None:
    for name in (
        "ck_evidence_items_provider_item_type_allowlist",
        "ck_evidence_items_flags_consistent",
    ):
        op.drop_constraint(name, "evidence_items", type_="check")
    types = "'marketaux_news','finnhub_quote','eia_energy_timeseries','sec_filing'"
    news = "'marketaux_news'"
    if expanded:
        types += ",'finnhub_company_news'"
        news += ",'finnhub_company_news'"
    op.create_check_constraint(
        "ck_evidence_items_provider_item_type_allowlist",
        "evidence_items",
        f"provider_item_type IN ({types})",
    )
    op.create_check_constraint(
        "ck_evidence_items_flags_consistent",
        "evidence_items",
        f"""
      (provider_item_type IN ({news}) AND news_signal_flag AND NOT official_source_flag AND NOT market_data_flag AND NOT disclosure_flag) OR
      (provider_item_type='finnhub_quote' AND market_data_flag AND NOT official_source_flag AND NOT disclosure_flag AND NOT news_signal_flag) OR
      (provider_item_type='eia_energy_timeseries' AND official_source_flag AND NOT market_data_flag AND NOT disclosure_flag AND NOT news_signal_flag) OR
      (provider_item_type='sec_filing' AND official_source_flag AND disclosure_flag AND NOT market_data_flag AND NOT news_signal_flag)
    """,
    )


def _guards(expanded: bool) -> None:
    extra = (
        "WHEN 'finnhub:company_news' THEN 'finnhub_company_news' WHEN 'eia:electricity_rto_region_data' THEN 'eia_energy_timeseries'"
        if expanded
        else ""
    )
    op.execute(f"""
    CREATE OR REPLACE FUNCTION r8a_evidence_projection_link_guard() RETURNS trigger AS $$
    DECLARE p safe_fact_projections%ROWTYPE; r raw_items%ROWTYPE;
            e evidence_items%ROWTYPE; c content_items%ROWTYPE; expected_type text;
    BEGIN
      SELECT * INTO p FROM safe_fact_projections WHERE id=NEW.safe_fact_projection_id;
      IF NOT FOUND OR p.processing_status <> 'ready' THEN RAISE EXCEPTION 'evidence_projection_not_ready'; END IF;
      expected_type := CASE p.provider || ':' || p.operation_key
        WHEN 'marketaux:news_all' THEN 'marketaux_news'
        WHEN 'finnhub:quote' THEN 'finnhub_quote'
        WHEN 'eia:electricity_retail_sales' THEN 'eia_energy_timeseries'
        WHEN 'sec_edgar:submissions_recent' THEN 'sec_filing' {extra} ELSE NULL END;
      IF expected_type IS NULL AND NEW.evidence_item_id IS NOT NULL THEN RAISE EXCEPTION 'evidence_projection_operation_unknown'; END IF;
      SELECT * INTO r FROM raw_items WHERE id=p.raw_item_id;
      IF NEW.evidence_item_id IS NOT NULL THEN
        SELECT * INTO e FROM evidence_items WHERE id=NEW.evidence_item_id;
        IF NOT FOUND OR e.raw_item_id IS DISTINCT FROM p.raw_item_id OR e.source_id IS DISTINCT FROM r.source_id
          OR e.source_account_id IS DISTINCT FROM r.source_account_id OR e.provider IS DISTINCT FROM p.provider
          OR e.provider_item_type IS DISTINCT FROM expected_type
          OR (e.content_item_id IS NOT NULL AND e.content_item_id IS DISTINCT FROM NEW.content_item_id)
        THEN RAISE EXCEPTION 'evidence_projection_evidence_provenance_mismatch'; END IF;
      END IF;
      IF NEW.content_item_id IS NOT NULL THEN
        SELECT * INTO c FROM content_items WHERE id=NEW.content_item_id;
        IF NOT FOUND OR c.raw_item_id IS DISTINCT FROM p.raw_item_id OR c.source_id IS DISTINCT FROM r.source_id
          OR c.source_account_id IS DISTINCT FROM r.source_account_id
        THEN RAISE EXCEPTION 'evidence_projection_content_provenance_mismatch'; END IF;
        IF expected_type IN ('finnhub_quote','eia_energy_timeseries') THEN RAISE EXCEPTION 'evidence_projection_content_forbidden'; END IF;
        IF expected_type IN ('marketaux_news','finnhub_company_news') AND c.content_kind <> 'article'
        THEN RAISE EXCEPTION 'evidence_projection_content_kind_mismatch'; END IF;
        IF expected_type='sec_filing' AND (c.content_kind <> 'official_release' OR c.body_availability <> 'unavailable'
          OR c.canonical_url IS NULL OR c.original_url IS DISTINCT FROM c.canonical_url OR NOT (
            c.canonical_url=p.factual_payload->>'official_url' OR EXISTS (
              SELECT 1 FROM evidence_projection_links prior JOIN safe_fact_projections pp ON pp.id=prior.safe_fact_projection_id
              WHERE prior.status='linked' AND prior.evidence_item_id=NEW.evidence_item_id
                AND prior.content_item_id=NEW.content_item_id AND pp.factual_payload->>'official_url'=c.canonical_url)))
        THEN RAISE EXCEPTION 'evidence_projection_sec_content_invalid'; END IF;
      END IF;
      IF expected_type IN ('finnhub_quote','eia_energy_timeseries') AND e.content_item_id IS NOT NULL
      THEN RAISE EXCEPTION 'evidence_projection_content_forbidden'; END IF;
      IF TG_OP='UPDATE' AND OLD.status='linked' AND (
        NEW.safe_fact_projection_id IS DISTINCT FROM OLD.safe_fact_projection_id OR NEW.evidence_item_id IS DISTINCT FROM OLD.evidence_item_id
        OR NEW.content_item_id IS DISTINCT FROM OLD.content_item_id OR NEW.status IS DISTINCT FROM OLD.status
        OR NEW.linked_at IS DISTINCT FROM OLD.linked_at)
      THEN RAISE EXCEPTION 'evidence_projection_link_immutable'; END IF;
      RETURN NEW;
    END; $$ LANGUAGE plpgsql
    """)
    forbidden = (
        "NEW.provider='eia' OR (NEW.provider='finnhub' AND NEW.provider_item_type <> 'finnhub_company_news')"
        if expanded
        else "NEW.provider IN ('finnhub','eia')"
    )
    op.execute(f"""
    CREATE OR REPLACE FUNCTION r8a_evidence_content_policy_guard() RETURNS trigger AS $$
    BEGIN
      IF ({forbidden}) AND NEW.content_item_id IS NOT NULL THEN RAISE EXCEPTION 'evidence_projection_content_forbidden'; END IF;
      RETURN NEW;
    END; $$ LANGUAGE plpgsql
    """)


def upgrade() -> None:
    op.add_column("collection_runs", sa.Column("resolved_window", JSONB(), nullable=True))
    op.create_check_constraint(
        "ck_collection_runs_resolved_window",
        "collection_runs",
        "resolved_window IS NULL OR (jsonb_typeof(resolved_window)='object' AND resolved_window ?& ARRAY['start','end'] AND (resolved_window - 'start' - 'end')='{}'::jsonb AND jsonb_typeof(resolved_window->'start')='string' AND jsonb_typeof(resolved_window->'end')='string' AND (resolved_window->>'start') ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}(T[0-9]{2})?$' AND (resolved_window->>'end') ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}(T[0-9]{2})?$')",
    )
    op.execute("""CREATE FUNCTION m2_run_window_guard() RETURNS trigger AS $$ BEGIN
      IF OLD.resolved_window IS NOT NULL AND NEW.resolved_window IS DISTINCT FROM OLD.resolved_window
      THEN RAISE EXCEPTION 'collection_run_window_immutable'; END IF; RETURN NEW;
      END; $$ LANGUAGE plpgsql""")
    op.execute("""CREATE TRIGGER trg_m2_run_window_guard BEFORE UPDATE ON collection_runs
      FOR EACH ROW EXECUTE FUNCTION m2_run_window_guard();""")
    op.execute("""
    CREATE FUNCTION m2_continuation_guard() RETURNS trigger AS $$
    DECLARE t collection_targets%ROWTYPE; provider_key text; state_keys text[];
    BEGIN
      IF NEW.target_id IS NULL OR NEW.continuation IS NULL
        OR NEW.continuation = 'null'::jsonb THEN RETURN NEW; END IF;
      SELECT * INTO t FROM collection_targets WHERE id=NEW.target_id;
      IF NOT FOUND OR t.provider_contract_version <> 2 THEN RETURN NEW; END IF;
      SELECT access_method INTO provider_key FROM sources WHERE id=t.source_id;
      IF jsonb_typeof(NEW.continuation) <> 'object'
        OR ARRAY(SELECT jsonb_object_keys(NEW.continuation) ORDER BY 1)
          <> ARRAY['config_hash','lineage','operation','provider','resolved_window','state','version']
        OR NEW.continuation->>'version' <> '1'
        OR NEW.continuation->>'provider' IS DISTINCT FROM provider_key
        OR NEW.continuation->>'operation' IS DISTINCT FROM t.operation_key
        OR COALESCE(NEW.continuation->>'config_hash','') !~ '^[0-9a-f]{64}$'
        OR jsonb_typeof(NEW.continuation->'resolved_window') <> 'object'
        OR ARRAY(SELECT jsonb_object_keys(NEW.continuation->'resolved_window') ORDER BY 1)
          <> ARRAY['end','start']
        OR NEW.continuation->'lineage' IS DISTINCT FROM jsonb_build_object(
          'target_id',t.id::text,
          'config_revision',t.config_revision,
          'operation_key',t.operation_key,
          'operation_config_version',t.operation_config_version,
          'provider_contract_version',t.provider_contract_version,
          'cursor_version',t.cursor_version,
          'run_mode',NEW.run_mode::text)
        OR jsonb_typeof(NEW.continuation->'state') <> 'object'
      THEN RAISE EXCEPTION 'collection_continuation_contract_invalid'; END IF;
      state_keys := ARRAY(SELECT jsonb_object_keys(NEW.continuation->'state') ORDER BY 1);
      IF (provider_key='marketaux' AND t.operation_key='news_all' AND state_keys NOT IN (ARRAY[]::text[],ARRAY['page']))
        OR (provider_key='finnhub' AND t.operation_key='company_news' AND state_keys NOT IN (ARRAY[]::text[],ARRAY['last_key']))
        OR (provider_key='eia' AND t.operation_key IN ('electricity_retail_sales','electricity_rto_region_data') AND state_keys NOT IN (ARRAY[]::text[],ARRAY['offset']))
        OR (provider_key='sec_edgar' AND t.operation_key='submissions_recent'
            AND state_keys NOT IN (ARRAY[]::text[],ARRAY['file'],ARRAY['file','files'],
              ARRAY['file','files','last_key'],ARRAY['file','last_key']))
      THEN RAISE EXCEPTION 'collection_continuation_state_invalid'; END IF;
      RETURN NEW;
    END; $$ LANGUAGE plpgsql
    """)
    op.execute("""CREATE TRIGGER trg_m2_continuation_guard
      BEFORE INSERT OR UPDATE OF continuation,target_id,run_mode,cursor_version,cursor_type
      ON collection_cursors FOR EACH ROW EXECUTE FUNCTION m2_continuation_guard();""")
    for name in ("request_count", "page_count"):
        op.add_column(
            "collection_runs", sa.Column(name, sa.Integer(), nullable=False, server_default="0")
        )
        op.create_check_constraint(
            f"ck_collection_runs_{name}_nonnegative", "collection_runs", f"{name} >= 0"
        )
    op.add_column(
        "raw_item_observations",
        sa.Column("observation_key", sa.String(64), nullable=False, server_default="run"),
    )
    op.drop_constraint("uq_raw_item_observations_run_item", "raw_item_observations", type_="unique")
    op.create_unique_constraint(
        "uq_raw_item_observations_run_item",
        "raw_item_observations",
        ["collection_run_id", "raw_item_id", "observation_key"],
    )
    _checks(True)
    _guards(True)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.execute(
        sa.text("SELECT EXISTS(SELECT 1 FROM raw_item_observations WHERE observation_key <> 'run')")
    ).scalar_one():
        raise RuntimeError("migration_0009_incompatible_observation_state")
    if bind.execute(
        sa.text("""SELECT EXISTS(SELECT 1 FROM evidence_items WHERE provider_item_type='finnhub_company_news')
      OR EXISTS(SELECT 1 FROM safe_fact_projections WHERE operation_key IN ('company_news','electricity_rto_region_data'))
      OR EXISTS(SELECT 1 FROM collection_targets WHERE provider_contract_version=2)
      OR EXISTS(SELECT 1 FROM collection_runs WHERE resolved_window IS NOT NULL)""")
    ).scalar_one():
        raise RuntimeError("migration_0009_incompatible_operation_state")
    _guards(False)
    _checks(False)
    op.execute("DROP TRIGGER trg_m2_continuation_guard ON collection_cursors")
    op.execute("DROP FUNCTION m2_continuation_guard()")
    op.execute("DROP TRIGGER trg_m2_run_window_guard ON collection_runs")
    op.execute("DROP FUNCTION m2_run_window_guard()")
    op.drop_constraint("ck_collection_runs_resolved_window", "collection_runs", type_="check")
    op.drop_column("collection_runs", "resolved_window")
    for name in ("request_count", "page_count"):
        op.drop_constraint(
            f"ck_collection_runs_{name}_nonnegative", "collection_runs", type_="check"
        )
        op.drop_column("collection_runs", name)
    op.drop_constraint("uq_raw_item_observations_run_item", "raw_item_observations", type_="unique")
    op.create_unique_constraint(
        "uq_raw_item_observations_run_item",
        "raw_item_observations",
        ["collection_run_id", "raw_item_id"],
    )
    op.drop_column("raw_item_observations", "observation_key")
