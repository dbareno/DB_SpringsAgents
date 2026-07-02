"""
app/db/session.py
─────────────────────────────────────────────────────────────────────────────
Async SQLAlchemy engine + session factory.

Uses ``asyncpg`` as the DBAPI driver (bundled with ``asyncpg`` extra of
``sqlalchemy``).  All database access in the application should go through
the ``get_db_session`` dependency or the ``db_session`` context manager.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.settings import get_settings

_settings = get_settings()

# ── Engine ─────────────────────────────────────────────────────────────────
engine: AsyncEngine = create_async_engine(
    _settings.postgres_url,
    echo=_settings.debug,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
)

# ── Session factory ─────────────────────────────────────────────────────────
AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# ── FastAPI dependency ──────────────────────────────────────────────────────
async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency that yields an async database session.

    Usage
    -----
    >>> @router.get("/example")
    ... async def example(db: AsyncSession = Depends(get_db_session)):
    ...     ...
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ── Context manager (for use outside FastAPI) ───────────────────────────────
@asynccontextmanager
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Async context manager for database sessions outside of FastAPI.

    Usage
    -----
    >>> async with db_session() as session:
    ...     result = await session.execute(select(SpringMaterial))
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
