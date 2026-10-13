import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from research_agent.discovery.repository import SqlDiscoveryRepository


@pytest.mark.integration
@pytest.mark.asyncio
async def test_throttle_serializes_slots_across_database_sessions(
    migrated_database_url: str,
) -> None:
    engine = create_async_engine(migrated_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    provider = f"arxiv-test-{uuid4()}"
    now = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)

    async def reserve() -> datetime:
        async with factory() as session:
            return await SqlDiscoveryRepository(session).reserve_request(
                provider=provider,
                now=now,
                interval=timedelta(seconds=3),
            )

    first, second = await asyncio.gather(reserve(), reserve())
    await engine.dispose()

    assert sorted((first, second)) == [now, now + timedelta(seconds=3)]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_search_cache_is_shared_and_expires(
    migrated_database_url: str,
) -> None:
    engine = create_async_engine(migrated_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    cache_key = uuid4().hex
    now = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)
    payload = {"topic": "agent memory", "items": []}

    async with factory() as writer_session:
        await SqlDiscoveryRepository(writer_session).save_cached_search(
            cache_key=cache_key,
            provider="arxiv",
            query="agent memory",
            response=payload,
            expires_at=now + timedelta(hours=24),
        )
    async with factory() as reader_session:
        repository = SqlDiscoveryRepository(reader_session)
        fresh = await repository.get_cached_search(cache_key=cache_key, now=now)
        expired = await repository.get_cached_search(
            cache_key=cache_key, now=now + timedelta(days=2)
        )

    await engine.dispose()

    assert fresh == payload
    assert expired is None
