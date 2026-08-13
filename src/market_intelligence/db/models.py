from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CHAR,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from market_intelligence.db.base import Base


class SourceType(enum.StrEnum):
    NEWS = "news"
    X = "x"
    OFFICIAL = "official"
    RSS = "rss"
    API = "api"
    WEB = "web"


class AuthorizationStatus(enum.StrEnum):
    PLANNED = "planned"
    ACCESS_TBD = "access_tbd"
    AUTHORIZED = "authorized"
    IMPLEMENTED = "implemented"
    DEGRADED = "degraded"
    BLOCKED = "blocked"
    DISABLED = "disabled"


class IdentityStatus(enum.StrEnum):
    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    CHANGED = "changed"
    DISABLED = "disabled"


class CollectionRunStatus(enum.StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"


class ParseStatus(enum.StrEnum):
    PENDING = "pending"
    PARSED = "parsed"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED = "skipped"


class ContentKind(enum.StrEnum):
    ARTICLE = "article"
    X_POST = "x_post"
    OFFICIAL_RELEASE = "official_release"
    FEED_ENTRY = "feed_entry"


class BodyAvailability(enum.StrEnum):
    FULL = "full"
    PARTIAL = "partial"
    SUMMARY_ONLY = "summary_only"
    UNAVAILABLE = "unavailable"


class DeletedStatus(enum.StrEnum):
    UNKNOWN = "unknown"
    PRESENT = "present"
    DELETED = "deleted"


class NotificationPriority(enum.StrEnum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    P4 = "P4"


class NotificationChannel(enum.StrEnum):
    TELEGRAM_PUSH = "telegram_push"


class NotificationStatus(enum.StrEnum):
    PENDING = "pending"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"
    SUPPRESSED = "suppressed"


class OutboxStatus(enum.StrEnum):
    PENDING = "pending"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"


class EventCandidateStatus(enum.StrEnum):
    CANDIDATE = "candidate"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


def enum_values(enum_class: type[enum.StrEnum]) -> list[str]:
    return [member.value for member in enum_class]


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (
        CheckConstraint("char_length(btrim(code)) > 0", name="ck_sources_code_not_blank"),
        CheckConstraint(
            "schedule_seconds IS NULL OR schedule_seconds > 0",
            name="ck_sources_schedule_seconds_positive",
        ),
        CheckConstraint(
            "consecutive_failures >= 0",
            name="ck_sources_consecutive_failures_nonnegative",
        ),
        Index("uq_sources_code", "code", unique=True),
        Index("ix_sources_enabled_source_type", "enabled", "source_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    code: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(255))
    source_type: Mapped[SourceType] = mapped_column(
        Enum(
            SourceType,
            name="source_type",
            values_callable=enum_values,
        )
    )
    access_method: Mapped[str] = mapped_column(String(100))
    authorization_status: Mapped[AuthorizationStatus] = mapped_column(
        Enum(
            AuthorizationStatus,
            name="authorization_status",
            values_callable=enum_values,
        )
    )
    retention_class: Mapped[str] = mapped_column(String(50))
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    schedule_seconds: Mapped[int | None] = mapped_column(Integer)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consecutive_failures: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=text("CURRENT_TIMESTAMP"),
    )

    accounts: Mapped[list[SourceAccount]] = relationship(back_populates="source")
    collection_runs: Mapped[list[CollectionRun]] = relationship(back_populates="source")
    raw_items: Mapped[list[RawItem]] = relationship(back_populates="source")
    content_items: Mapped[list[ContentItem]] = relationship(back_populates="source")
    evidence_items: Mapped[list[EvidenceItem]] = relationship(back_populates="source")


class SourceAccount(Base):
    __tablename__ = "source_accounts"
    __table_args__ = (
        Index(
            "uq_source_accounts_source_external_id",
            "source_id",
            "external_id",
            unique=True,
            postgresql_where=text("external_id IS NOT NULL"),
        ),
        Index("ix_source_accounts_source_id", "source_id"),
        Index("ix_source_accounts_source_enabled", "source_id", "enabled"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id", ondelete="RESTRICT")
    )
    external_id: Mapped[str | None] = mapped_column(String(255))
    handle: Mapped[str | None] = mapped_column(String(255))
    display_name: Mapped[str | None] = mapped_column(String(255))
    endpoint: Mapped[str | None] = mapped_column(Text)
    identity_status: Mapped[IdentityStatus] = mapped_column(
        Enum(
            IdentityStatus,
            name="identity_status",
            values_callable=enum_values,
        )
    )
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    collection_options: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    last_identity_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=text("CURRENT_TIMESTAMP"),
    )

    source: Mapped[Source] = relationship(back_populates="accounts")
    cursors: Mapped[list[CollectionCursor]] = relationship(back_populates="source_account")
    collection_runs: Mapped[list[CollectionRun]] = relationship(back_populates="source_account")
    raw_items: Mapped[list[RawItem]] = relationship(back_populates="source_account")
    content_items: Mapped[list[ContentItem]] = relationship(back_populates="source_account")
    evidence_items: Mapped[list[EvidenceItem]] = relationship(back_populates="source_account")


class CollectionCursor(Base):
    __tablename__ = "collection_cursors"
    __table_args__ = (
        Index(
            "uq_collection_cursors_account_type",
            "source_account_id",
            "cursor_type",
            unique=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    source_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_accounts.id", ondelete="RESTRICT")
    )
    cursor_type: Mapped[str] = mapped_column(String(100))
    cursor_value: Mapped[str | None] = mapped_column(Text)
    last_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=text("CURRENT_TIMESTAMP"),
    )

    source_account: Mapped[SourceAccount] = relationship(back_populates="cursors")


class CollectionRun(Base):
    __tablename__ = "collection_runs"
    __table_args__ = (
        CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at",
            name="ck_collection_runs_finished_not_before_started",
        ),
        CheckConstraint("fetched_count >= 0", name="ck_collection_runs_fetched_nonnegative"),
        CheckConstraint("new_count >= 0", name="ck_collection_runs_new_nonnegative"),
        CheckConstraint(
            "duplicate_count >= 0",
            name="ck_collection_runs_duplicate_nonnegative",
        ),
        CheckConstraint("error_count >= 0", name="ck_collection_runs_error_nonnegative"),
        CheckConstraint("retry_count >= 0", name="ck_collection_runs_retry_nonnegative"),
        Index("ix_collection_runs_source_started", "source_id", "started_at"),
        Index("ix_collection_runs_status_started", "status", "started_at"),
        Index("ix_collection_runs_source_account_id", "source_account_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id", ondelete="RESTRICT")
    )
    source_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_accounts.id", ondelete="RESTRICT")
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[CollectionRunStatus] = mapped_column(
        Enum(
            CollectionRunStatus,
            name="collection_run_status",
            values_callable=enum_values,
        )
    )
    fetched_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    new_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    duplicate_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    error_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message_redacted: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))

    source: Mapped[Source] = relationship(back_populates="collection_runs")
    source_account: Mapped[SourceAccount | None] = relationship(back_populates="collection_runs")
    raw_items: Mapped[list[RawItem]] = relationship(back_populates="collection_run")


class RawItem(Base):
    __tablename__ = "raw_items"
    __table_args__ = (
        CheckConstraint(
            "http_status IS NULL OR http_status BETWEEN 100 AND 599",
            name="ck_raw_items_http_status_range",
        ),
        Index("ix_raw_items_collection_run_id", "collection_run_id"),
        Index("ix_raw_items_source_collection_run", "source_id", "collection_run_id"),
        Index("ix_raw_items_source_external_id", "source_id", "external_id"),
        Index("ix_raw_items_source_fetched", "source_id", "fetched_at"),
        Index("ix_raw_items_payload_hash", "payload_hash"),
        Index("ix_raw_items_parse_status", "parse_status"),
        Index("uq_raw_items_id_source_id", "id", "source_id", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id", ondelete="RESTRICT")
    )
    source_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_accounts.id", ondelete="RESTRICT")
    )
    collection_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("collection_runs.id", ondelete="RESTRICT")
    )
    external_id: Mapped[str | None] = mapped_column(String(255))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    http_status: Mapped[int | None] = mapped_column(Integer)
    content_type: Mapped[str | None] = mapped_column(String(255))
    payload_location: Mapped[str | None] = mapped_column(Text)
    payload_hash: Mapped[str | None] = mapped_column(String(128))
    retention_class: Mapped[str] = mapped_column(String(50))
    parse_status: Mapped[ParseStatus] = mapped_column(
        Enum(
            ParseStatus,
            name="parse_status",
            values_callable=enum_values,
        )
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP")
    )

    source: Mapped[Source] = relationship(back_populates="raw_items")
    source_account: Mapped[SourceAccount | None] = relationship(back_populates="raw_items")
    collection_run: Mapped[CollectionRun] = relationship(back_populates="raw_items")
    content_item: Mapped[ContentItem | None] = relationship(back_populates="raw_item")
    evidence_items: Mapped[list[EvidenceItem]] = relationship(
        back_populates="raw_item", overlaps="evidence_items"
    )


class ContentItem(Base):
    __tablename__ = "content_items"
    __table_args__ = (
        Index(
            "uq_content_items_source_external_id",
            "source_id",
            "external_id",
            unique=True,
            postgresql_where=text("external_id IS NOT NULL"),
        ),
        Index(
            "uq_content_items_canonical_url",
            "canonical_url",
            unique=True,
            postgresql_where=text("canonical_url IS NOT NULL"),
        ),
        Index(
            "uq_content_items_source_content_hash",
            "source_id",
            "content_hash",
            unique=True,
            postgresql_where=text("content_hash IS NOT NULL"),
        ),
        Index("uq_content_items_raw_item_id", "raw_item_id", unique=True),
        Index("ix_content_items_source_published_at", "source_published_at"),
        Index("ix_content_items_first_seen_at", "first_seen_at"),
        Index("ix_content_items_source_account_id", "source_account_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    raw_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("raw_items.id", ondelete="RESTRICT")
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id", ondelete="RESTRICT")
    )
    source_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_accounts.id", ondelete="RESTRICT")
    )
    content_kind: Mapped[ContentKind] = mapped_column(
        Enum(
            ContentKind,
            name="content_kind",
            values_callable=enum_values,
        )
    )
    external_id: Mapped[str | None] = mapped_column(String(255))
    title: Mapped[str | None] = mapped_column(Text)
    source_summary: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str | None] = mapped_column(Text)
    body_availability: Mapped[BodyAvailability] = mapped_column(
        Enum(
            BodyAvailability,
            name="body_availability",
            values_callable=enum_values,
        )
    )
    author: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(String(35))
    original_url: Mapped[str | None] = mapped_column(Text)
    canonical_url: Mapped[str | None] = mapped_column(Text)
    source_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    content_hash: Mapped[str | None] = mapped_column(String(128))
    reply_to_external_id: Mapped[str | None] = mapped_column(String(255))
    quote_external_id: Mapped[str | None] = mapped_column(String(255))
    repost_external_id: Mapped[str | None] = mapped_column(String(255))
    deleted_status: Mapped[DeletedStatus] = mapped_column(
        Enum(
            DeletedStatus,
            name="deleted_status",
            values_callable=enum_values,
        )
    )
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=text("CURRENT_TIMESTAMP"),
    )

    raw_item: Mapped[RawItem] = relationship(back_populates="content_item")
    source: Mapped[Source] = relationship(back_populates="content_items")
    source_account: Mapped[SourceAccount | None] = relationship(back_populates="content_items")
    notifications: Mapped[list[Notification]] = relationship(back_populates="content_item")
    evidence_items: Mapped[list[EvidenceItem]] = relationship(back_populates="content_item")


class EvidenceItem(Base):
    __tablename__ = "evidence_items"
    __table_args__ = (
        CheckConstraint("evidence_version > 0", name="ck_evidence_items_evidence_version_positive"),
        CheckConstraint(
            "provider_item_hash ~ '^[0-9a-f]{64}$'",
            name="ck_evidence_items_provider_item_hash_lower_hex",
        ),
        CheckConstraint(
            "provider IN ('marketaux', 'finnhub', 'eia', 'sec_edgar')",
            name="ck_evidence_items_provider_allowlist",
        ),
        CheckConstraint(
            "provider_item_type IN "
            "('marketaux_news', 'finnhub_quote', 'eia_energy_timeseries', 'sec_filing')",
            name="ck_evidence_items_provider_item_type_allowlist",
        ),
        CheckConstraint(
            "evidence_kind IN ('news', 'market_data', 'energy_official', 'disclosure')",
            name="ck_evidence_items_evidence_kind_allowlist",
        ),
        CheckConstraint(
            "source_type IN ('news', 'market_data', 'official_energy', 'disclosure')",
            name="ck_evidence_items_source_type_allowlist",
        ),
        CheckConstraint(
            "access_level IN "
            "('public_fulltext', 'public_summary', 'subscription_required', 'licensed', "
            "'link_only', 'blocked', 'unknown')",
            name="ck_evidence_items_access_level_allowlist",
        ),
        CheckConstraint(
            "processing_status IN ('pending', 'validated', 'blocked', 'invalid')",
            name="ck_evidence_items_processing_status_allowlist",
        ),
        CheckConstraint(
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
        CheckConstraint(
            "jsonb_typeof(content_presence) = 'object'",
            name="ck_evidence_items_content_presence_object",
        ),
        CheckConstraint(
            "jsonb_typeof(numeric_presence) = 'object'",
            name="ck_evidence_items_numeric_presence_object",
        ),
        CheckConstraint(
            "jsonb_typeof(entity_refs) = 'array'",
            name="ck_evidence_items_entity_refs_array",
        ),
        CheckConstraint(
            "jsonb_typeof(asset_refs) = 'array'",
            name="ck_evidence_items_asset_refs_array",
        ),
        CheckConstraint(
            "jsonb_typeof(topic_refs) = 'array'",
            name="ck_evidence_items_topic_refs_array",
        ),
        CheckConstraint(
            "jsonb_typeof(errors) = 'array'",
            name="ck_evidence_items_errors_array",
        ),
        CheckConstraint(
            "raw_payload_reference IS NULL OR "
            "raw_payload_reference LIKE 'internal://%' OR "
            "raw_payload_reference LIKE 'capture://%' OR "
            "raw_payload_reference LIKE 'local-ref://%'",
            name="ck_evidence_items_raw_payload_reference_internal",
        ),
        CheckConstraint(
            "raw_payload_reference IS NULL OR "
            "(raw_payload_reference NOT LIKE 'http://%' "
            "AND raw_payload_reference NOT LIKE 'https://%')",
            name="ck_evidence_items_raw_payload_reference_not_http",
        ),
        CheckConstraint(
            "raw_payload_reference IS NULL OR ("
            "lower(raw_payload_reference) NOT LIKE '%api_key=%' AND "
            "lower(raw_payload_reference) NOT LIKE '%api_token=%' AND "
            "lower(raw_payload_reference) NOT LIKE '%token=%' AND "
            "lower(raw_payload_reference) NOT LIKE '%authorization%' AND "
            "lower(raw_payload_reference) NOT LIKE '%x-finnhub-token%')",
            name="ck_evidence_items_raw_payload_reference_no_secret_markers",
        ),
        ForeignKeyConstraint(
            ["raw_item_id", "source_id"],
            ["raw_items.id", "raw_items.source_id"],
            name="fk_evidence_items_raw_item_source",
            ondelete="RESTRICT",
        ),
        Index(
            "uq_evidence_items_provider_hash",
            "provider",
            "provider_item_hash",
            unique=True,
        ),
        Index(
            "uq_evidence_items_provider_item_id",
            "provider",
            "provider_item_id",
            unique=True,
            postgresql_where=text("provider_item_id IS NOT NULL"),
        ),
        Index("ix_evidence_items_raw_item_id", "raw_item_id"),
        Index("ix_evidence_items_source_id", "source_id"),
        Index("ix_evidence_items_source_account_id", "source_account_id"),
        Index("ix_evidence_items_content_item_id", "content_item_id"),
        Index("ix_evidence_items_event_time", "event_time"),
        Index("ix_evidence_items_observed_at", "observed_at"),
        Index("ix_evidence_items_processing_status", "processing_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    evidence_version: Mapped[int] = mapped_column(Integer)
    provider: Mapped[str] = mapped_column(String(64))
    provider_item_type: Mapped[str] = mapped_column(String(64))
    evidence_kind: Mapped[str] = mapped_column(String(64))
    source_type: Mapped[str] = mapped_column(String(64))
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id", ondelete="RESTRICT")
    )
    source_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_accounts.id", ondelete="RESTRICT")
    )
    raw_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    content_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_items.id", ondelete="RESTRICT")
    )
    provider_item_id: Mapped[str | None] = mapped_column(String(255))
    provider_item_hash: Mapped[str] = mapped_column(CHAR(64))
    event_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    access_level: Mapped[str] = mapped_column(String(64))
    processing_status: Mapped[str] = mapped_column(String(64))
    official_source_flag: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    market_data_flag: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    disclosure_flag: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    news_signal_flag: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    content_presence: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    numeric_presence: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    entity_refs: Mapped[list[Any]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    asset_refs: Mapped[list[Any]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    topic_refs: Mapped[list[Any]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    raw_payload_reference: Mapped[str | None] = mapped_column(String(512))
    errors: Mapped[list[Any]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=text("CURRENT_TIMESTAMP"),
    )

    source: Mapped[Source] = relationship(
        back_populates="evidence_items", overlaps="evidence_items"
    )
    source_account: Mapped[SourceAccount | None] = relationship(back_populates="evidence_items")
    raw_item: Mapped[RawItem] = relationship(
        back_populates="evidence_items",
        foreign_keys=[raw_item_id, source_id],
        overlaps="evidence_items,source",
    )
    content_item: Mapped[ContentItem | None] = relationship(back_populates="evidence_items")
    event_candidate_links: Mapped[list[EventCandidateEvidence]] = relationship(
        back_populates="evidence_item"
    )


class EventCandidate(Base):
    __tablename__ = "event_candidates"
    __table_args__ = (
        CheckConstraint("cluster_key ~ '^[0-9a-f]{64}$'", name="ck_event_candidates_cluster_key"),
        CheckConstraint(
            "anchor_value_hash ~ '^[0-9a-f]{64}$'",
            name="ck_event_candidates_anchor_value_hash",
        ),
        CheckConstraint(
            "strong_identity_hash IS NULL OR strong_identity_hash ~ '^[0-9a-f]{64}$'",
            name="ck_event_candidates_strong_identity_hash",
        ),
        CheckConstraint("evidence_count >= 0", name="ck_event_candidates_evidence_nonnegative"),
        CheckConstraint("source_count >= 0", name="ck_event_candidates_source_nonnegative"),
        CheckConstraint("confidence BETWEEN 0 AND 1", name="ck_event_candidates_confidence"),
        CheckConstraint(
            "importance_score BETWEEN 0 AND 100", name="ck_event_candidates_importance"
        ),
        CheckConstraint("latest_seen_at >= first_seen_at", name="ck_event_candidates_seen_order"),
        Index("uq_event_candidates_cluster_key", "cluster_key", unique=True),
        Index("ix_event_candidates_status_latest", "status", "latest_seen_at"),
        Index("ix_event_candidates_strong_identity", "strong_identity_hash"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    cluster_key: Mapped[str] = mapped_column(CHAR(64))
    anchor_type: Mapped[str] = mapped_column(String(64))
    anchor_value_hash: Mapped[str] = mapped_column(CHAR(64))
    strong_identity_hash: Mapped[str | None] = mapped_column(CHAR(64))
    identity_signatures: Mapped[list[Any]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )
    title_fingerprints: Mapped[list[Any]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    event_type: Mapped[str] = mapped_column(String(64))
    status: Mapped[EventCandidateStatus] = mapped_column(
        Enum(
            EventCandidateStatus,
            name="event_candidate_status",
            values_callable=enum_values,
        )
    )
    canonical_title: Mapped[str | None] = mapped_column(Text)
    fact_summary: Mapped[str | None] = mapped_column(Text)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    latest_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    primary_entity: Mapped[str | None] = mapped_column(String(255))
    entities: Mapped[list[Any]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    companies: Mapped[list[Any]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    assets: Mapped[list[Any]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    sectors: Mapped[list[Any]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    topics: Mapped[list[Any]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    evidence_count: Mapped[int] = mapped_column(Integer)
    source_count: Mapped[int] = mapped_column(Integer)
    confidence: Mapped[float] = mapped_column(Float)
    importance_score: Mapped[float] = mapped_column(Float)
    importance_reasons: Mapped[list[Any]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=text("CURRENT_TIMESTAMP"),
    )

    evidence_links: Mapped[list[EventCandidateEvidence]] = relationship(
        back_populates="event_candidate"
    )


class EventCandidateEvidence(Base):
    __tablename__ = "event_candidate_evidence"
    __table_args__ = (
        CheckConstraint("char_length(btrim(match_rule)) > 0", name="ck_event_evidence_rule"),
        CheckConstraint("rule_version > 0", name="ck_event_evidence_rule_version"),
        CheckConstraint(
            "(active AND removed_at IS NULL) OR (NOT active AND removed_at IS NOT NULL)",
            name="ck_event_evidence_active_removed",
        ),
        Index(
            "uq_event_candidate_evidence_active_pair",
            "event_candidate_id",
            "evidence_item_id",
            unique=True,
            postgresql_where=text("active"),
        ),
        Index("ix_event_candidate_evidence_item", "evidence_item_id"),
        Index("ix_event_candidate_evidence_active", "event_candidate_id", "active"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    event_candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("event_candidates.id", ondelete="RESTRICT"),
    )
    evidence_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("evidence_items.id", ondelete="RESTRICT"),
    )
    match_rule: Mapped[str] = mapped_column(String(100))
    rule_version: Mapped[int] = mapped_column(Integer)
    official_source: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP")
    )
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    event_candidate: Mapped[EventCandidate] = relationship(back_populates="evidence_links")
    evidence_item: Mapped[EvidenceItem] = relationship(back_populates="event_candidate_links")


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        CheckConstraint("payload_version > 0", name="ck_notifications_payload_version_positive"),
        CheckConstraint("retry_count >= 0", name="ck_notifications_retry_nonnegative"),
        Index("uq_notifications_dedup_key", "dedup_key", unique=True),
        Index("ix_notifications_status_scheduled", "status", "scheduled_at"),
        Index("ix_notifications_content_item_id", "content_item_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    content_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_items.id", ondelete="RESTRICT")
    )
    priority: Mapped[NotificationPriority] = mapped_column(
        Enum(
            NotificationPriority,
            name="notification_priority",
            values_callable=enum_values,
        )
    )
    priority_reason: Mapped[str] = mapped_column(Text)
    policy_rule_id: Mapped[str] = mapped_column(String(100))
    policy_version: Mapped[str] = mapped_column(String(100))
    channel: Mapped[NotificationChannel] = mapped_column(
        Enum(
            NotificationChannel,
            name="notification_channel",
            values_callable=enum_values,
        )
    )
    dedup_key: Mapped[str] = mapped_column(String(255))
    payload_version: Mapped[int] = mapped_column(Integer)
    status: Mapped[NotificationStatus] = mapped_column(
        Enum(
            NotificationStatus,
            name="notification_status",
            values_callable=enum_values,
        )
    )
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_code: Mapped[str | None] = mapped_column(String(100))
    retry_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP")
    )

    content_item: Mapped[ContentItem | None] = relationship(back_populates="notifications")


class OutboxMessage(Base):
    __tablename__ = "outbox_messages"
    __table_args__ = (
        CheckConstraint("attempts >= 0", name="ck_outbox_messages_attempts_nonnegative"),
        Index("uq_outbox_messages_idempotency_key", "idempotency_key", unique=True),
        Index("ix_outbox_messages_status_available", "status", "available_at"),
        Index(
            "ix_outbox_messages_aggregate",
            "aggregate_type",
            "aggregate_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    idempotency_key: Mapped[str] = mapped_column(String(255))
    aggregate_type: Mapped[str] = mapped_column(String(100))
    aggregate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    message_type: Mapped[str] = mapped_column(String(100))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    status: Mapped[OutboxStatus] = mapped_column(
        Enum(
            OutboxStatus,
            name="outbox_status",
            values_callable=enum_values,
        )
    )
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP")
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index(
            "ix_audit_logs_target_created",
            "target_type",
            "target_id",
            "created_at",
        ),
        Index(
            "ix_audit_logs_actor_created",
            "actor_type",
            "actor_id",
            "created_at",
        ),
        Index("ix_audit_logs_action", "action"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    actor_type: Mapped[str] = mapped_column(String(100))
    actor_id: Mapped[str | None] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(100))
    target_type: Mapped[str] = mapped_column(String(100))
    target_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP")
    )
