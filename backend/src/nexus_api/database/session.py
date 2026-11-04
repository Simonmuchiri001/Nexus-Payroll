"""Async engine and request-scoped transaction management."""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from nexus_api.config import get_settings

_settings = get_settings()
engine: AsyncEngine = create_async_engine(
    _settings.database_url,
    echo=_settings.database_echo,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)
SessionFactory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield a session inside one database transaction.

    Commits on successful request completion; rolls back on any exception.
    Services call ``flush`` when they need generated values, never ``commit``.
    """

    async with SessionFactory() as session, session.begin():
        yield session


async def check_database() -> bool:
    """Readiness probe that proves a usable database connection exists."""

    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception:
        return False
    return True
