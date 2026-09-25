"""Value-free M2-C bundle contracts."""

from __future__ import annotations

import uuid
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BundleBuildResult:
    status: str
    event_candidate_id: uuid.UUID
    bundle_id: uuid.UUID | None
    revision: int | None
    safe_error_code: str | None = None


@dataclass(frozen=True, slots=True)
class BundleWorkerReport:
    discovered: int
    claimed: int
    ready: int
    partial: int
    unchanged: int
    blocked: int
    retried: int
    recovered: int


class BundleConflict(ValueError):
    """A value-free permanent bundle contract failure."""
