from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from research_agent.config import get_settings


class Base(DeclarativeBase):
    pass


def build_async_engine(database_url: str | None = None) -> AsyncEngine:
    return create_async_engine(
        database_url or get_settings().database_url,
        pool_pre_ping=True,
    )


def build_session_factory(
    engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


engine = build_async_engine()
async_session_factory = build_session_factory(engine)
