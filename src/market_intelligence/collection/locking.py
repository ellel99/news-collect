from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Any, cast
from uuid import uuid4

from redis.asyncio import Redis

from market_intelligence.collection.contracts import CollectionTarget

_RENEW_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('pexpire', KEYS[1], ARGV[2])
end
return 0
"""
_RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
"""


def target_lock_key(target: CollectionTarget) -> str:
    identity = str(target.source_account_id) if target.source_account_id else "source"
    return f"collection:lock:{target.source_id}:{identity}"


def retry_marker_key(collection_run_id: str) -> str:
    return f"collection:retry:{collection_run_id}"


@dataclass(slots=True)
class TargetLock:
    redis: Redis
    key: str
    owner_token: str
    ttl_seconds: int

    @classmethod
    def for_target(cls, redis: Redis, target: CollectionTarget, ttl_seconds: int) -> TargetLock:
        return cls(redis, target_lock_key(target), uuid4().hex, ttl_seconds)

    async def acquire(self) -> bool:
        return bool(
            await self.redis.set(
                self.key,
                self.owner_token,
                nx=True,
                px=self.ttl_seconds * 1000,
            )
        )

    async def renew(self) -> bool:
        result = await cast(
            Awaitable[Any],
            self.redis.eval(
                _RENEW_SCRIPT,
                1,
                self.key,
                self.owner_token,
                str(self.ttl_seconds * 1000),
            ),
        )
        return bool(result)

    async def release(self) -> bool:
        result = await cast(
            Awaitable[Any],
            self.redis.eval(_RELEASE_SCRIPT, 1, self.key, self.owner_token),
        )
        return bool(result)

    async def exists(self) -> bool:
        return bool(await self.redis.exists(self.key))
