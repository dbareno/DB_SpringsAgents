"""
tests/test_seed_materials.py
─────────────────────────────────────────────────────────────────────────────
Integration test for scripts/seed_materials.py against a temporary
in-memory SQLite database — verifies the seed populates the table and is
idempotent (safe to re-run without duplicating rows).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import SpringMaterial
from scripts import seed_materials


@pytest.fixture
async def temp_engine_and_session(monkeypatch):
    """
    Builds a fresh in-memory SQLite engine/session factory and patches
    ``scripts.seed_materials``'s imported ``get_engine``/``db_session`` names
    so the seed script runs against it instead of the real app DB.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async def _fake_get_engine():
        return engine

    @asynccontextmanager
    async def _fake_db_session() -> AsyncGenerator[AsyncSession, None]:
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    monkeypatch.setattr(seed_materials, "get_engine", _fake_get_engine)
    monkeypatch.setattr(seed_materials, "db_session", _fake_db_session)

    yield engine, session_factory

    await engine.dispose()


class TestSeedMaterials:
    async def test_seed_populates_table(self, temp_engine_and_session) -> None:
        """First run inserts every row from MATERIALS_SEED."""
        _, session_factory = temp_engine_and_session

        inserted = await seed_materials.seed()

        assert inserted == len(seed_materials.MATERIALS_SEED)

        async with session_factory() as session:
            result = await session.execute(select(SpringMaterial))
            rows = result.scalars().all()
            assert len(rows) == len(seed_materials.MATERIALS_SEED)
            assert all(row.active is True for row in rows)

    async def test_seed_is_idempotent(self, temp_engine_and_session) -> None:
        """Re-running the seed does not duplicate or error on existing rows."""
        await seed_materials.seed()
        second_run_inserted = await seed_materials.seed()

        assert second_run_inserted == 0

        _, session_factory = temp_engine_and_session
        async with session_factory() as session:
            result = await session.execute(select(SpringMaterial))
            rows = result.scalars().all()
            assert len(rows) == len(seed_materials.MATERIALS_SEED)
