"""
app/db/session.py
─────────────────────────────────────────────────────────────────────────────
Async SQLAlchemy engine + session factory.

Auto-detects PostgreSQL vs SQLite:
1. Tries PostgreSQL (asyncpg) first.
2. If unavailable, falls back to SQLite (aiosqlite) and auto-creates tables.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.settings import get_settings

logger = logging.getLogger(__name__)

_settings = get_settings()

# ── Engine detection: PostgreSQL → SQLite fallback ──────────────────────

_ENGINE_URL: str | None = None
_engine: AsyncEngine | None = None
_AsyncSessionLocal: async_sessionmaker[AsyncSession] | None = None


async def _create_engine() -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """Create database engine — tries PostgreSQL first, falls back to SQLite."""
    global _ENGINE_URL

    # Try PostgreSQL
    pg_url = _settings.postgres_url
    try:
        pg_engine = create_async_engine(
            pg_url,
            echo=_settings.debug,
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,
        )
        # Quick connectivity check
        async with pg_engine.connect() as conn:
            await conn.execute(
                __import__("sqlalchemy").text("SELECT 1")
            )
        _ENGINE_URL = pg_url
        logger.info("Database: PostgreSQL (%s)", pg_url.split("@")[-1].split("/")[0])
        session_factory = async_sessionmaker(
            pg_engine, class_=AsyncSession, expire_on_commit=False
        )
        return pg_engine, session_factory
    except Exception as exc:
        logger.warning("PostgreSQL unavailable (%s). Falling back to SQLite.", exc)
        if pg_engine:
            await pg_engine.dispose()

    # Fallback: SQLite
    sqlite_url = "sqlite+aiosqlite:///spring_design_agent.db"
    sqlite_engine = create_async_engine(
        sqlite_url,
        echo=_settings.debug,
        connect_args={"check_same_thread": False},
    )
    _ENGINE_URL = sqlite_url

    # Auto-create tables
    from app.db.models import Base, DesignIteration
    async with sqlite_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Migrate: add approved column if missing (SQLite-specific)
    try:
        from sqlalchemy import text
        result = await conn.execute(
            text("SELECT approved FROM design_iterations LIMIT 1")
        )
    except Exception:
        # Column does not exist → add it
        await conn.execute(
            text("ALTER TABLE design_iterations ADD COLUMN approved BOOLEAN NOT NULL DEFAULT 0")
        )
        logger.info("[Migration] Added `approved` column to design_iterations.")

    logger.info("Database: SQLite (spring_design_agent.db)")
    session_factory = async_sessionmaker(
        sqlite_engine, class_=AsyncSession, expire_on_commit=False
    )
    return sqlite_engine, session_factory


async def get_engine() -> AsyncEngine:
    """Return the cached engine, creating it on first call."""
    global _engine, _AsyncSessionLocal
    if _engine is None:
        _engine, _AsyncSessionLocal = await _create_engine()
    return _engine


async def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the cached session factory, creating engine on first call."""
    global _engine, _AsyncSessionLocal
    if _AsyncSessionLocal is None:
        _engine, _AsyncSessionLocal = await _create_engine()
    return _AsyncSessionLocal


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
    factory = await get_session_factory()
    async with factory() as session:
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
    factory = await get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
