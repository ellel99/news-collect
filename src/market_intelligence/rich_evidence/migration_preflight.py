"""Value-free typed preflight required before applying Alembic revision 0010."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from market_intelligence.evidence.provider_mappings import (
    LEGACY_OPAQUE_IDENTITY_OPERATIONS,
    legacy_provider_item_identity,
)
from market_intelligence.providers.operation_policy import factual_operation_policy
from market_intelligence.safe_projection.contracts import (
    ProjectionContractError,
    canonical_projection_hash,
    normalize_and_classify_factual_payload,
)

_PAGE_SIZE = 500


async def validate_0010_pre_migration(engine: AsyncEngine) -> tuple[dict[str, object], int]:
    checked = 0
    cursor: str | None = None
    safe_errors: set[str] = set()
    canonical_candidates: dict[str, int] = {}
    async with engine.connect() as connection:
        pgcrypto_available = bool(
            await connection.scalar(
                text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname='pgcrypto')")
            )
        )
        if not pgcrypto_available:
            return {
                "status": "BLOCKED",
                "checked_linked_projection_count": 0,
                "safe_errors": ["migration_0010_pgcrypto_required"],
            }, 2
        await connection.commit()
        transaction = await connection.begin()
        try:
            await connection.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            )
            while True:
                rows = (
                    (
                        await connection.execute(
                            text("""
                            SELECT p.id::text,p.provider,p.operation_key,
                                   p.projection_schema_version,p.factual_payload,
                                   p.projection_hash,p.quality_status::text,
                                   o.provider_contract_version,o.observed_at,
                                   r.retention_class,s.retention_class AS source_retention,
                                   e.id::text AS evidence_id,e.provider_item_id,
                                   e.provider_item_hash,e.provider_item_type,e.evidence_kind,
                                   e.source_type,e.access_level,e.event_time,
                                   e.observed_at AS evidence_observed_at,
                                   e.official_source_flag,e.market_data_flag,e.disclosure_flag,
                                   e.news_signal_flag,e.content_presence,e.numeric_presence
                            FROM evidence_projection_links l
                            JOIN safe_fact_projections p ON p.id=l.safe_fact_projection_id
                            JOIN raw_item_observations o ON o.id=p.observation_id
                            JOIN raw_items r ON r.id=p.raw_item_id
                            JOIN sources s ON s.id=r.source_id
                            JOIN evidence_items e ON e.id=l.evidence_item_id
                            WHERE l.status='linked'
                              AND (CAST(:cursor AS text) IS NULL OR p.id::text > :cursor)
                            ORDER BY p.id::text
                            LIMIT :limit
                            """),
                            {"cursor": cursor, "limit": _PAGE_SIZE},
                        )
                    )
                    .mappings()
                    .all()
                )
                if not rows:
                    break
                for row in rows:
                    checked += 1
                    try:
                        normalized, quality = normalize_and_classify_factual_payload(
                            row["provider"],
                            row["operation_key"],
                            row["projection_schema_version"],
                            row["factual_payload"],
                        )
                        policy = factual_operation_policy(
                            row["provider"],
                            row["operation_key"],
                            row["provider_contract_version"],
                        )
                        plain_identity = str(normalized["provider_item_id"])
                        legacy_allowed = (
                            row["provider"],
                            row["operation_key"],
                        ) in LEGACY_OPAQUE_IDENTITY_OPERATIONS
                        legacy_identity = (
                            legacy_provider_item_identity(
                                row["provider"], row["operation_key"], normalized
                            )
                            if legacy_allowed
                            else plain_identity
                        )
                        adopted_legacy = (
                            legacy_allowed
                            and row["provider_item_id"] == legacy_identity
                            and legacy_identity != plain_identity
                        )
                        expected_access = "link_only" if adopted_legacy else policy.access
                        expected_content = {
                            "has_title": bool(normalized.get("title")),
                            "has_body": False,
                            "has_url": bool(
                                normalized.get("canonical_url") or normalized.get("official_url")
                            ),
                            "has_snippet": False,
                            "has_description": False,
                        }
                        is_market = row["operation_key"] == "quote"
                        expected_numeric = {
                            "has_numeric_value": is_market or row["provider"] == "eia",
                            "numeric_field_count": (
                                7 if is_market else 1 if row["provider"] == "eia" else 0
                            ),
                            "nullable_allowed": row["provider"] == "eia",
                        }
                        is_canonical_candidate = (
                            row["event_time"] is not None
                            and row["event_time"].isoformat() == normalized["published_at"]
                            and row["evidence_observed_at"] == row["observed_at"]
                        )
                        canonical_candidates.setdefault(row["evidence_id"], 0)
                        canonical_candidates[row["evidence_id"]] += int(is_canonical_candidate)
                        if (
                            normalized != row["factual_payload"]
                            or canonical_projection_hash(normalized) != row["projection_hash"]
                            or quality != row["quality_status"]
                            or row["retention_class"] != row["source_retention"]
                            or row["retention_class"] not in policy.retention
                            or row["provider_item_id"] not in {plain_identity, legacy_identity}
                            or (
                                legacy_allowed
                                and legacy_identity != plain_identity
                                and row["provider_item_id"] == legacy_identity
                                and not adopted_legacy
                            )
                            or row["provider_item_type"] != policy.item_type
                            or row["evidence_kind"] != policy.evidence_kind
                            or row["source_type"] != policy.source_type
                            or row["access_level"] != expected_access
                            or row["official_source_flag"]
                            != (row["provider"] in {"eia", "sec_edgar"})
                            or row["market_data_flag"] != is_market
                            or row["disclosure_flag"] != (row["provider"] == "sec_edgar")
                            or row["news_signal_flag"]
                            != (row["operation_key"] in {"news_all", "company_news"})
                            or (
                                is_canonical_candidate
                                and row["content_presence"] != expected_content
                            )
                            or (
                                is_canonical_candidate
                                and row["numeric_presence"] != expected_numeric
                            )
                            or (
                                is_canonical_candidate
                                and not adopted_legacy
                                and row["provider_item_hash"] != row["projection_hash"]
                            )
                        ):
                            raise ProjectionContractError("projection_not_canonical")
                    except (ProjectionContractError, ValueError, TypeError):
                        safe_errors.add("migration_0010_projection_contract_invalid")
                cursor = rows[-1]["id"]
                if len(rows) < _PAGE_SIZE:
                    break
            if any(count != 1 for count in canonical_candidates.values()):
                safe_errors.add("migration_0010_canonical_evidence_ambiguous")
        except Exception:
            safe_errors.add("migration_0010_preflight_failed")
        finally:
            await transaction.rollback()
    report: dict[str, Any] = {
        "status": "PASS" if not safe_errors else "BLOCKED",
        "checked_linked_projection_count": checked,
        "safe_errors": sorted(safe_errors),
    }
    return report, 0 if not safe_errors else 2
