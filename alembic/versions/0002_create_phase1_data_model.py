"""Create the Phase 1 source registry and data model."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

source_type = postgresql.ENUM(
    "news", "x", "official", "rss", "api", "web", name="source_type", create_type=False
)
authorization_status = postgresql.ENUM(
    "planned",
    "access_tbd",
    "authorized",
    "implemented",
    "degraded",
    "blocked",
    "disabled",
    name="authorization_status",
    create_type=False,
)
identity_status = postgresql.ENUM(
    "unverified", "verified", "changed", "disabled", name="identity_status", create_type=False
)
collection_run_status = postgresql.ENUM(
    "running",
    "succeeded",
    "partial",
    "failed",
    name="collection_run_status",
    create_type=False,
)
parse_status = postgresql.ENUM(
    "pending", "parsed", "partial", "failed", "skipped", name="parse_status", create_type=False
)
content_kind = postgresql.ENUM(
    "article",
    "x_post",
    "official_release",
    "feed_entry",
    name="content_kind",
    create_type=False,
)
body_availability = postgresql.ENUM(
    "full",
    "partial",
    "summary_only",
    "unavailable",
    name="body_availability",
    create_type=False,
)
deleted_status = postgresql.ENUM(
    "unknown", "present", "deleted", name="deleted_status", create_type=False
)
notification_priority = postgresql.ENUM(
    "P0", "P1", "P2", "P3", "P4", name="notification_priority", create_type=False
)
notification_channel = postgresql.ENUM(
    "telegram_push", name="notification_channel", create_type=False
)
notification_status = postgresql.ENUM(
    "pending",
    "sending",
    "sent",
    "failed",
    "suppressed",
    name="notification_status",
    create_type=False,
)
outbox_status = postgresql.ENUM(
    "pending", "publishing", "published", "failed", name="outbox_status", create_type=False
)

ENUMS = (
    source_type,
    authorization_status,
    identity_status,
    collection_run_status,
    parse_status,
    content_kind,
    body_availability,
    deleted_status,
    notification_priority,
    notification_channel,
    notification_status,
    outbox_status,
)


def uuid_pk() -> sa.Column[object]:
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        server_default=sa.text("gen_random_uuid()"),
        primary_key=True,
    )


def created_at() -> sa.Column[object]:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        server_default=sa.text("CURRENT_TIMESTAMP"),
        nullable=False,
    )


def updated_at() -> sa.Column[object]:
    return sa.Column(
        "updated_at",
        sa.DateTime(timezone=True),
        server_default=sa.text("CURRENT_TIMESTAMP"),
        nullable=False,
    )


def upgrade() -> None:
    bind = op.get_bind()
    for enum_type in ENUMS:
        enum_type.create(bind, checkfirst=False)

    op.create_table(
        "sources",
        uuid_pk(),
        sa.Column("code", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("source_type", source_type, nullable=False),
        sa.Column("access_method", sa.String(length=100), nullable=False),
        sa.Column("authorization_status", authorization_status, nullable=False),
        sa.Column("retention_class", sa.String(length=50), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("schedule_seconds", sa.Integer(), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "consecutive_failures", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        created_at(),
        updated_at(),
        sa.CheckConstraint(
            "char_length(btrim(code)) > 0",
            name="ck_sources_code_not_blank",
        ),
        sa.CheckConstraint(
            "schedule_seconds IS NULL OR schedule_seconds > 0",
            name="ck_sources_schedule_seconds_positive",
        ),
        sa.CheckConstraint(
            "consecutive_failures >= 0",
            name="ck_sources_consecutive_failures_nonnegative",
        ),
    )
    op.create_index("uq_sources_code", "sources", ["code"], unique=True)
    op.create_index(
        "ix_sources_enabled_source_type", "sources", ["enabled", "source_type"], unique=False
    )

    op.create_table(
        "source_accounts",
        uuid_pk(),
        sa.Column(
            "source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sources.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("handle", sa.String(length=255), nullable=True),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("endpoint", sa.Text(), nullable=True),
        sa.Column("identity_status", identity_status, nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "collection_options",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("last_identity_check_at", sa.DateTime(timezone=True), nullable=True),
        created_at(),
        updated_at(),
    )
    op.create_index(
        "uq_source_accounts_source_external_id",
        "source_accounts",
        ["source_id", "external_id"],
        unique=True,
        postgresql_where=sa.text("external_id IS NOT NULL"),
    )
    op.create_index("ix_source_accounts_source_id", "source_accounts", ["source_id"])
    op.create_index(
        "ix_source_accounts_source_enabled", "source_accounts", ["source_id", "enabled"]
    )

    op.create_table(
        "collection_cursors",
        uuid_pk(),
        sa.Column(
            "source_account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("source_accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("cursor_type", sa.String(length=100), nullable=False),
        sa.Column("cursor_value", sa.Text(), nullable=True),
        sa.Column("last_published_at", sa.DateTime(timezone=True), nullable=True),
        updated_at(),
    )
    op.create_index(
        "uq_collection_cursors_account_type",
        "collection_cursors",
        ["source_account_id", "cursor_type"],
        unique=True,
    )

    op.create_table(
        "collection_runs",
        uuid_pk(),
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
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", collection_run_status, nullable=False),
        sa.Column("fetched_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("new_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("duplicate_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message_redacted", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at",
            name="ck_collection_runs_finished_not_before_started",
        ),
        sa.CheckConstraint("fetched_count >= 0", name="ck_collection_runs_fetched_nonnegative"),
        sa.CheckConstraint("new_count >= 0", name="ck_collection_runs_new_nonnegative"),
        sa.CheckConstraint("duplicate_count >= 0", name="ck_collection_runs_duplicate_nonnegative"),
        sa.CheckConstraint("error_count >= 0", name="ck_collection_runs_error_nonnegative"),
        sa.CheckConstraint("retry_count >= 0", name="ck_collection_runs_retry_nonnegative"),
    )
    op.create_index(
        "ix_collection_runs_source_started", "collection_runs", ["source_id", "started_at"]
    )
    op.create_index(
        "ix_collection_runs_status_started", "collection_runs", ["status", "started_at"]
    )
    op.create_index(
        "ix_collection_runs_source_account_id", "collection_runs", ["source_account_id"]
    )

    op.create_table(
        "raw_items",
        uuid_pk(),
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
            "collection_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("collection_runs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("content_type", sa.String(length=255), nullable=True),
        sa.Column("payload_location", sa.Text(), nullable=True),
        sa.Column("payload_hash", sa.String(length=128), nullable=True),
        sa.Column("retention_class", sa.String(length=50), nullable=False),
        sa.Column("parse_status", parse_status, nullable=False),
        created_at(),
        sa.CheckConstraint(
            "http_status IS NULL OR http_status BETWEEN 100 AND 599",
            name="ck_raw_items_http_status_range",
        ),
    )
    op.create_index("ix_raw_items_collection_run_id", "raw_items", ["collection_run_id"])
    op.create_index(
        "ix_raw_items_source_collection_run",
        "raw_items",
        ["source_id", "collection_run_id"],
    )
    op.create_index("ix_raw_items_source_external_id", "raw_items", ["source_id", "external_id"])
    op.create_index("ix_raw_items_source_fetched", "raw_items", ["source_id", "fetched_at"])
    op.create_index("ix_raw_items_payload_hash", "raw_items", ["payload_hash"])
    op.create_index("ix_raw_items_parse_status", "raw_items", ["parse_status"])

    op.create_table(
        "content_items",
        uuid_pk(),
        sa.Column(
            "raw_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("raw_items.id", ondelete="RESTRICT"),
            nullable=False,
        ),
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
        sa.Column("content_kind", content_kind, nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("source_summary", sa.Text(), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("body_availability", body_availability, nullable=False),
        sa.Column("author", sa.Text(), nullable=True),
        sa.Column("language", sa.String(length=35), nullable=True),
        sa.Column("original_url", sa.Text(), nullable=True),
        sa.Column("canonical_url", sa.Text(), nullable=True),
        sa.Column("source_published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_hash", sa.String(length=128), nullable=True),
        sa.Column("reply_to_external_id", sa.String(length=255), nullable=True),
        sa.Column("quote_external_id", sa.String(length=255), nullable=True),
        sa.Column("repost_external_id", sa.String(length=255), nullable=True),
        sa.Column("deleted_status", deleted_status, nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        created_at(),
        updated_at(),
    )
    op.create_index("uq_content_items_raw_item_id", "content_items", ["raw_item_id"], unique=True)
    op.create_index(
        "uq_content_items_source_external_id",
        "content_items",
        ["source_id", "external_id"],
        unique=True,
        postgresql_where=sa.text("external_id IS NOT NULL"),
    )
    op.create_index(
        "uq_content_items_canonical_url",
        "content_items",
        ["canonical_url"],
        unique=True,
        postgresql_where=sa.text("canonical_url IS NOT NULL"),
    )
    op.create_index(
        "uq_content_items_source_content_hash",
        "content_items",
        ["source_id", "content_hash"],
        unique=True,
        postgresql_where=sa.text("content_hash IS NOT NULL"),
    )
    op.create_index(
        "ix_content_items_source_published_at", "content_items", ["source_published_at"]
    )
    op.create_index("ix_content_items_first_seen_at", "content_items", ["first_seen_at"])
    op.create_index("ix_content_items_source_account_id", "content_items", ["source_account_id"])

    op.create_table(
        "notifications",
        uuid_pk(),
        sa.Column(
            "content_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("content_items.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("priority", notification_priority, nullable=False),
        sa.Column("priority_reason", sa.Text(), nullable=False),
        sa.Column("policy_rule_id", sa.String(length=100), nullable=False),
        sa.Column("policy_version", sa.String(length=100), nullable=False),
        sa.Column("channel", notification_channel, nullable=False),
        sa.Column("dedup_key", sa.String(length=255), nullable=False),
        sa.Column("payload_version", sa.Integer(), nullable=False),
        sa.Column("status", notification_status, nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(length=100), nullable=True),
        sa.Column("retry_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        created_at(),
        sa.CheckConstraint("payload_version > 0", name="ck_notifications_payload_version_positive"),
        sa.CheckConstraint("retry_count >= 0", name="ck_notifications_retry_nonnegative"),
    )
    op.create_index("uq_notifications_dedup_key", "notifications", ["dedup_key"], unique=True)
    op.create_index(
        "ix_notifications_status_scheduled", "notifications", ["status", "scheduled_at"]
    )
    op.create_index("ix_notifications_content_item_id", "notifications", ["content_item_id"])

    op.create_table(
        "outbox_messages",
        uuid_pk(),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("aggregate_type", sa.String(length=100), nullable=False),
        sa.Column("aggregate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("message_type", sa.String(length=100), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("status", outbox_status, nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        created_at(),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("attempts >= 0", name="ck_outbox_messages_attempts_nonnegative"),
    )
    op.create_index(
        "uq_outbox_messages_idempotency_key",
        "outbox_messages",
        ["idempotency_key"],
        unique=True,
    )
    op.create_index(
        "ix_outbox_messages_status_available",
        "outbox_messages",
        ["status", "available_at"],
    )
    op.create_index(
        "ix_outbox_messages_aggregate",
        "outbox_messages",
        ["aggregate_type", "aggregate_id"],
    )

    op.create_table(
        "audit_logs",
        uuid_pk(),
        sa.Column("actor_type", sa.String(length=100), nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("target_type", sa.String(length=100), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("before", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        created_at(),
    )
    op.create_index(
        "ix_audit_logs_target_created",
        "audit_logs",
        ["target_type", "target_id", "created_at"],
    )
    op.create_index(
        "ix_audit_logs_actor_created",
        "audit_logs",
        ["actor_type", "actor_id", "created_at"],
    )
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_table("outbox_messages")
    op.drop_table("notifications")
    op.drop_table("content_items")
    op.drop_table("raw_items")
    op.drop_table("collection_runs")
    op.drop_table("collection_cursors")
    op.drop_table("source_accounts")
    op.drop_table("sources")

    bind = op.get_bind()
    for enum_type in reversed(ENUMS):
        enum_type.drop(bind, checkfirst=False)
