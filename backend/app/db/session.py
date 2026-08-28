"""Async SQLAlchemy engine and session factory."""

from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    """Create the process-wide async database engine."""

    return create_async_engine(get_settings().database_url, pool_pre_ping=True)


@lru_cache(maxsize=1)
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Create the process-wide async session factory."""

    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def dispose_engine() -> None:
    """Dispose the process-wide engine when the application shuts down."""

    if get_engine.cache_info().currsize:
        await get_engine().dispose()
