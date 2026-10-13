import asyncio
from collections.abc import Mapping
from typing import Literal

from minio import Minio
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from research_agent.config import Settings


DependencyState = Literal["ok", "unavailable"]
ReadinessStates = Mapping[str, DependencyState]


async def check_readiness(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
) -> ReadinessStates:
    """Probe required infrastructure without returning sensitive details."""

    results = await asyncio.gather(
        _check_postgres(session_factory),
        _check_redis(settings.redis_url),
        _check_object_storage(settings),
    )
    return dict(zip(("postgres", "redis", "object_storage"), results, strict=True))


async def _check_postgres(
    session_factory: async_sessionmaker[AsyncSession],
) -> DependencyState:
    try:
        async with asyncio.timeout(3):
            async with session_factory() as session:
                await session.execute(text("SELECT 1"))
    except Exception:
        return "unavailable"
    return "ok"


async def _check_redis(redis_url: str) -> DependencyState:
    client = Redis.from_url(redis_url, socket_connect_timeout=3, socket_timeout=3)
    try:
        async with asyncio.timeout(3):
            await client.ping()
    except Exception:
        return "unavailable"
    finally:
        await client.aclose()
    return "ok"


async def _check_object_storage(settings: Settings) -> DependencyState:
    client = Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=False,
        region=settings.minio_region,
    )

    def probe() -> bool:
        return client.bucket_exists(settings.minio_bucket)

    try:
        # A successful False response still proves the private endpoint and
        # credentials are reachable; the upload path creates the bucket lazily.
        async with asyncio.timeout(3):
            await asyncio.to_thread(probe)
    except Exception:
        return "unavailable"
    return "ok"
