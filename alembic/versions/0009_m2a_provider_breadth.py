"""M2-A operation-specific Evidence policy (expand only)."""
# ruff: noqa: E501

from collections.abc import Sequence

import sqlalchemy as sa

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
      OR EXISTS(SELECT 1 FROM collection_targets WHERE provider_contract_version=2)""")
    ).scalar_one():
        raise RuntimeError("migration_0009_incompatible_operation_state")
    _guards(False)
    _checks(False)
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
