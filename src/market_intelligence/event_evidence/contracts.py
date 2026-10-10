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
    claim_lost: int


class BundleConflict(ValueError):
    """A value-free permanent bundle contract failure."""


class BundleRetryableConflict(RuntimeError):
    """A value-free transient bundle dependency or membership race."""


class BundleClaimLost(RuntimeError):
    """The durable worker claim was recovered or replaced before persistence."""
