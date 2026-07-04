"""
tests/conftest.py
─────────────────────────────────────────────────────────────────────────────
Shared fixtures for the Spring Design Agent test suite.

All database interactions are mocked — no real database required.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Base, DesignProject, SpringMaterial


def _build_execute_chain(
    scalar_one_or_none_value: object = None,
    scalars_all_value: list[object] | None = None,
) -> tuple[MagicMock, AsyncMock]:
    """
    Construye un result_mock con dos caminos paralelos:

    - ``result.scalar_one_or_none()`` → retorna ``scalar_one_or_none_value``
    - ``result.scalars().all()`` → retorna ``scalars_all_value``
      (default [])
    """
    if scalars_all_value is None:
        scalars_all_value = []

    scalar_mock = MagicMock()
    scalar_mock.all.return_value = scalars_all_value

    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = scalar_one_or_none_value
    result_mock.scalars.return_value = scalar_mock

    return result_mock, scalar_mock


@pytest.fixture
def mock_db_session() -> AsyncMock:
    """
    Retorna un AsyncSession mockeado con un execute() estable.

    Reemplaza ``execute`` con un AsyncMock fijo para evitar la creacion
    de un nuevo mock en cada acceso.
    """
    session = AsyncMock(spec=AsyncSession)

    # Reemplazar execute con un AsyncMock estable
    execute_mock = AsyncMock()
    session.execute = execute_mock  # type: ignore[method-assign]

    # Metodos basicos como no-op
    session.add.return_value = None
    session.add_all.return_value = None
    session.flush = AsyncMock(return_value=None)
    session.commit = AsyncMock(return_value=None)
    session.rollback = AsyncMock(return_value=None)

    # Por defecto: execute retorna un result donde:
    # - scalar_one_or_none() → None (sesion no encontrada)
    # - scalars().all() → [] (lista vacia)
    result_mock, _ = _build_execute_chain(
        scalar_one_or_none_value=None,
        scalars_all_value=[],
    )
    execute_mock.return_value = result_mock

    return session


@pytest.fixture
def mock_project() -> DesignProject:
    """
    Retorna una instancia real de DesignProject con valores minimos.
    """
    return DesignProject(
        id=1,
        session_id="test-session",
        raw_user_input="Design a spring",
        spring_type="compression",
        status="approved",
        final_report={"status": "approved", "summary": "Design OK"},
        total_iterations=1,
    )


@pytest.fixture
def mock_graph_final_state() -> dict:
    """
    Retorna un estado final simulado de LangGraph con diseno aprobado.
    """
    from app.schemas.state import ComplianceReport, MaterialProperties, SpringGeometry

    geometry = SpringGeometry(
        wire_diameter_mm=3.5,
        mean_coil_diameter_mm=28.0,
        outer_diameter_mm=31.5,
        inner_diameter_mm=24.5,
        active_coils=8.0,
        total_coils=10.0,
        free_length_mm=60.0,
        pitch_mm=7.5,
        spring_index=8.0,
        spring_rate_n_mm=5.0,
    )
    compliance = ComplianceReport(
        approved=True,
        safety_factor_shear=2.1,
        safety_factor_buckling=1.8,
        safety_factor_fatigue=None,
        applicable_standard="DIN 2095",
        failure_modes=[],
        redesign_directives=[],
    )
    material = MaterialProperties(
        material_id=1,
        name="ASTM A228 Music Wire",
        shear_modulus_gpa=79.3,
        elastic_modulus_gpa=207.0,
        density_kg_m3=7850.0,
        yield_strength_mpa=1500.0,
        ultimate_strength_mpa=1800.0,
        max_temp_c=150.0,
        corrosion_resistant=False,
        cost_usd_per_kg=3.80,
    )

    return {
        "final_report": {
            "status": "approved",
            "spring_type": "compression",
            "summary": "Design approved with safety factor 2.1.",
        },
        "iteration_count": 1,
        "compliance": compliance,
        "geometry": geometry,
        "material": material,
        "errors": [],
    }


@pytest.fixture
def mock_graph_clarify_state() -> dict:
    """
    Retorna un estado final simulado de LangGraph que requiere clarificacion.
    """
    return {
        "final_report": {
            "status": "needs_clarification",
            "clarification_questions": [
                "What is the required load force in Newtons?",
                "What is the maximum allowed outer diameter?",
            ],
            "partial_requirements": {"spring_type": "compression"},
        },
        "iteration_count": 0,
        "compliance": None,
        "geometry": None,
        "material": None,
        "errors": [],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Seeded in-memory SQLite (materials catalogue) — used by tool/API tests that
# need a real DB round-trip instead of a mocked AsyncSession.
# ─────────────────────────────────────────────────────────────────────────────

_TEST_MATERIALS: list[dict[str, object]] = [
    {
        "name": "ASTM A228 Music Wire",
        "standard": "ASTM A228",
        "shear_modulus_gpa": 81.5,
        "elastic_modulus_gpa": 207.0,
        "density_kg_m3": 7850.0,
        "yield_strength_mpa": 1580.0,
        "ultimate_strength_mpa": 1900.0,
        "max_temp_c": 120.0,
        "corrosion_resistant": False,
        "cost_usd_per_kg": 3.80,
        "active": True,
    },
    {
        "name": "ASTM A313 Type 302 Stainless Steel",
        "standard": "ASTM A313",
        "shear_modulus_gpa": 69.0,
        "elastic_modulus_gpa": 193.0,
        "density_kg_m3": 7920.0,
        "yield_strength_mpa": 1100.0,
        "ultimate_strength_mpa": 1380.0,
        "max_temp_c": 260.0,
        "corrosion_resistant": True,
        "cost_usd_per_kg": 9.50,
        "active": True,
    },
    {
        "name": "Inconel 718 (High-Temp)",
        "standard": "AMS 5596",
        "shear_modulus_gpa": 77.0,
        "elastic_modulus_gpa": 200.0,
        "density_kg_m3": 8190.0,
        "yield_strength_mpa": 1100.0,
        "ultimate_strength_mpa": 1380.0,
        "max_temp_c": 590.0,
        "corrosion_resistant": True,
        "cost_usd_per_kg": 95.00,
        "active": True,
    },
    {
        "name": "Retired Test Alloy",
        "standard": "N/A",
        "shear_modulus_gpa": 70.0,
        "elastic_modulus_gpa": 190.0,
        "density_kg_m3": 7800.0,
        "yield_strength_mpa": 900.0,
        "ultimate_strength_mpa": 1000.0,
        "max_temp_c": 100.0,
        "corrosion_resistant": False,
        "cost_usd_per_kg": 1.0,
        "active": False,
    },
]


@pytest.fixture
async def seeded_materials_engine():
    """
    Creates an in-memory SQLite async engine, creates the schema, and seeds
    a small representative materials catalogue (including one inactive
    material to exercise the soft-delete filter).

    Yields the ``async_sessionmaker`` so tests can build sessions directly,
    or patch ``app.db.session.get_session_factory`` to return it.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as session:
        for data in _TEST_MATERIALS:
            session.add(SpringMaterial(**data))
        await session.commit()

    yield session_factory

    await engine.dispose()


@pytest.fixture
def patch_materials_db_session(monkeypatch, seeded_materials_engine):
    """
    Patches ``app.tools.materials.db_session`` to yield sessions from the
    seeded in-memory SQLite engine, so ``query_material_properties_tool``
    performs a real (but isolated) DB round-trip in tests.
    """

    @asynccontextmanager
    async def _fake_db_session() -> AsyncGenerator[AsyncSession, None]:
        async with seeded_materials_engine() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    monkeypatch.setattr("app.tools.materials.db_session", _fake_db_session)
    return seeded_materials_engine
