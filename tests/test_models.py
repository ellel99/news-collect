from market_intelligence.db import Base

PHASE1_TABLES = {
    "sources",
    "source_accounts",
    "collection_cursors",
    "collection_runs",
    "raw_items",
    "content_items",
    "notifications",
    "outbox_messages",
    "audit_logs",
}

FUTURE_TABLE_NAMES = {
    "events",
    "event_versions",
    "evidence_links",
    "analyses",
    "asset_impacts",
    "portfolio_accounts",
    "holdings",
    "investment_plans",
    "plan_rules",
    "candidate_rules",
    "plan_reviews",
}


def test_metadata_contains_only_phase1_business_tables() -> None:
    assert set(Base.metadata.tables) == PHASE1_TABLES | {"system_metadata"}
    assert set(Base.metadata.tables).isdisjoint(FUTURE_TABLE_NAMES)


def test_raw_item_requires_collection_run_and_indexes_it() -> None:
    table = Base.metadata.tables["raw_items"]
    assert not table.c.collection_run_id.nullable
    assert table.c.collection_run_id.foreign_keys
    assert {index.name for index in table.indexes} >= {
        "ix_raw_items_collection_run_id",
        "ix_raw_items_source_collection_run",
    }


def test_outbox_idempotency_key_is_unique() -> None:
    table = Base.metadata.tables["outbox_messages"]
    assert not table.c.idempotency_key.nullable
    assert any(
        index.unique and {column.name for column in index.columns} == {"idempotency_key"}
        for index in table.indexes
    )


def test_content_item_uses_source_summary() -> None:
    table = Base.metadata.tables["content_items"]
    assert "source_summary" in table.c
    assert "summary" not in table.c
    assert "ai_summary" not in table.c
