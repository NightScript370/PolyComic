from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings, require_database_url


@lru_cache
def get_engine() -> AsyncEngine:
    """Created lazily so importing the app never connects to the database."""
    url = require_database_url(get_settings().database_url)
    return create_async_engine(url, pool_pre_ping=True)


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as session:
        yield session


async def dispose_engine() -> None:
    """Close pooled connections and forget the engine (shutdown / tests)."""
    if get_engine.cache_info().currsize:
        await get_engine().dispose()
    get_sessionmaker.cache_clear()
    get_engine.cache_clear()
