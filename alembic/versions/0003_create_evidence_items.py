"""Create the approved evidence persistence table."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "uq_raw_items_id_source_id",
        "raw_items",
        ["id", "source_id"],
        unique=True,
    )
    op.create_table(
        "evidence_items",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("evidence_version", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("provider_item_type", sa.String(length=64), nullable=False),
        sa.Column("evidence_kind", sa.String(length=64), nullable=False),
        sa.Column("source_type", sa.String(length=64), nullable=False),
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
        sa.Column(
            "raw_item_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "content_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("content_items.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("provider_item_id", sa.String(length=255), nullable=True),
        sa.Column("provider_item_hash", sa.CHAR(length=64), nullable=False),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("access_level", sa.String(length=64), nullable=False),
        sa.Column("processing_status", sa.String(length=64), nullable=False),
        sa.Column(
            "official_source_flag", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "market_data_flag", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("disclosure_flag", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "news_signal_flag", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "content_presence",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "numeric_presence",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "entity_refs",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "asset_refs",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "topic_refs",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("raw_payload_reference", sa.String(length=512), nullable=True),
        sa.Column(
            "errors",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
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
        sa.CheckConstraint(
            "evidence_version > 0", name="ck_evidence_items_evidence_version_positive"
        ),
        sa.CheckConstraint(
            "provider_item_hash ~ '^[0-9a-f]{64}$'",
            name="ck_evidence_items_provider_item_hash_lower_hex",
        ),
        sa.CheckConstraint(
            "provider IN ('marketaux', 'finnhub', 'eia', 'sec_edgar')",
            name="ck_evidence_items_provider_allowlist",
        ),
        sa.CheckConstraint(
            "provider_item_type IN "
            "('marketaux_news', 'finnhub_quote', 'eia_energy_timeseries', 'sec_filing')",
            name="ck_evidence_items_provider_item_type_allowlist",
        ),
        sa.CheckConstraint(
            "evidence_kind IN ('news', 'market_data', 'energy_official', 'disclosure')",
            name="ck_evidence_items_evidence_kind_allowlist",
        ),
        sa.CheckConstraint(
            "source_type IN ('news', 'market_data', 'official_energy', 'disclosure')",
            name="ck_evidence_items_source_type_allowlist",
        ),
        sa.CheckConstraint(
            "access_level IN "
            "('public_fulltext', 'public_summary', 'subscription_required', 'licensed', "
            "'link_only', 'blocked', 'unknown')",
            name="ck_evidence_items_access_level_allowlist",
        ),
        sa.CheckConstraint(
            "processing_status IN ('pending', 'validated', 'blocked', 'invalid')",
            name="ck_evidence_items_processing_status_allowlist",
        ),
        sa.CheckConstraint(
            "(provider_item_type = 'marketaux_news' AND news_signal_flag "
            "AND NOT official_source_flag AND NOT market_data_flag AND NOT disclosure_flag) OR "
            "(provider_item_type = 'finnhub_quote' AND market_data_flag "
            "AND NOT official_source_flag AND NOT disclosure_flag AND NOT news_signal_flag) OR "
            "(provider_item_type = 'eia_energy_timeseries' AND official_source_flag "
            "AND NOT market_data_flag AND NOT disclosure_flag AND NOT news_signal_flag) OR "
            "(provider_item_type = 'sec_filing' AND official_source_flag AND disclosure_flag "
            "AND NOT market_data_flag AND NOT news_signal_flag)",
            name="ck_evidence_items_flags_consistent",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(content_presence) = 'object'",
            name="ck_evidence_items_content_presence_object",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(numeric_presence) = 'object'",
            name="ck_evidence_items_numeric_presence_object",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(entity_refs) = 'array'",
            name="ck_evidence_items_entity_refs_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(asset_refs) = 'array'",
            name="ck_evidence_items_asset_refs_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(topic_refs) = 'array'",
            name="ck_evidence_items_topic_refs_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(errors) = 'array'",
            name="ck_evidence_items_errors_array",
        ),
        sa.CheckConstraint(
            "raw_payload_reference IS NULL OR "
            "raw_payload_reference LIKE 'internal://%' OR "
            "raw_payload_reference LIKE 'capture://%' OR "
            "raw_payload_reference LIKE 'local-ref://%'",
            name="ck_evidence_items_raw_payload_reference_internal",
        ),
        sa.CheckConstraint(
            "raw_payload_reference IS NULL OR "
            "(raw_payload_reference NOT LIKE 'http://%' "
            "AND raw_payload_reference NOT LIKE 'https://%')",
            name="ck_evidence_items_raw_payload_reference_not_http",
        ),
        sa.CheckConstraint(
            "raw_payload_reference IS NULL OR ("
            "lower(raw_payload_reference) NOT LIKE '%api_key=%' AND "
            "lower(raw_payload_reference) NOT LIKE '%api_token=%' AND "
            "lower(raw_payload_reference) NOT LIKE '%token=%' AND "
            "lower(raw_payload_reference) NOT LIKE '%authorization%' AND "
            "lower(raw_payload_reference) NOT LIKE '%x-finnhub-token%')",
            name="ck_evidence_items_raw_payload_reference_no_secret_markers",
        ),
        sa.ForeignKeyConstraint(
            ["raw_item_id", "source_id"],
            ["raw_items.id", "raw_items.source_id"],
            name="fk_evidence_items_raw_item_source",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "uq_evidence_items_provider_hash",
        "evidence_items",
        ["provider", "provider_item_hash"],
        unique=True,
    )
    op.create_index(
        "uq_evidence_items_provider_item_id",
        "evidence_items",
        ["provider", "provider_item_id"],
        unique=True,
        postgresql_where=sa.text("provider_item_id IS NOT NULL"),
    )
    for column in (
        "raw_item_id",
        "source_id",
        "source_account_id",
        "content_item_id",
        "event_time",
        "observed_at",
        "processing_status",
    ):
        op.create_index(f"ix_evidence_items_{column}", "evidence_items", [column])


def downgrade() -> None:
    op.drop_table("evidence_items")
    op.drop_index("uq_raw_items_id_source_id", table_name="raw_items")
