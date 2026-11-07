"""Async engine and request-scoped transaction management.

Serverless note: on Vercel each invocation is its own process, so the
default QueuePool (10 + 20 overflow) would exhaust a Neon compute with
concurrent cold starts. When `VERCEL` is set we use `NullPool` (one
short-lived connection per checkout) behind Neon's PgBouncer pooler.
Locally we keep a small queue for normal requests.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from nexus_api.config import get_settings

_settings = get_settings()

if os.environ.get("VERCEL"):
    _engine_kwargs: dict[str, object] = {"poolclass": NullPool}
else:
    _engine_kwargs = {"pool_pre_ping": True, "pool_size": 5, "max_overflow": 10}

engine: AsyncEngine = create_async_engine(
    _settings.database_url,
    echo=_settings.database_echo,
    **_engine_kwargs,  # pyright: ignore[reportArgumentType]
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
