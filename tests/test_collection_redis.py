import os
from uuid import uuid4

import pytest
import pytest_asyncio
from redis.asyncio import Redis

from market_intelligence.collection.contracts import CollectionTarget
from market_intelligence.collection.locking import TargetLock

REDIS_TEST_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/14")


@pytest_asyncio.fixture
async def redis_client() -> Redis:
    client = Redis.from_url(REDIS_TEST_URL, decode_responses=True)
    await client.flushdb()
    try:
        yield client
    finally:
        await client.flushdb()
        await client.aclose()


def target() -> CollectionTarget:
    return CollectionTarget(uuid4(), uuid4(), "rss", "fake", "metadata_only")


@pytest.mark.asyncio
async def test_owner_token_lock_competes_renews_and_releases(redis_client: Redis) -> None:
    value = target()
    owner = TargetLock.for_target(redis_client, value, 2)
    competitor = TargetLock.for_target(redis_client, value, 2)
    assert await owner.acquire()
    assert not await competitor.acquire()
    assert await owner.renew()
    assert not await competitor.renew()
    assert not await competitor.release()
    assert await owner.release()
    assert await competitor.acquire()
