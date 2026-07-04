"""
tests/test_repositories.py
─────────────────────────────────────────────────────────────────────────────
Unit tests for the database repository layer.

All database interactions are mocked via ``unittest.mock`` — no real
database connection is required to run these tests.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DesignIteration, DesignProject, SpringMaterial
from app.db.repositories.design_repository import (
    DesignIterationRepository,
    DesignProjectRepository,
)
from app.db.repositories.material_repository import MaterialRepository


# ─────────────────────────────────────────────────────────────────────────────
# MaterialRepository
# ─────────────────────────────────────────────────────────────────────────────


class TestMaterialRepository:
    """Tests para MaterialRepository."""

    async def test_get_by_id_returns_material(
        self, mock_db_session: AsyncMock
    ) -> None:
        """Verifica que get_by_id retorna el material cuando existe."""
        material = SpringMaterial(
            id=1,
            name="ASTM A228",
            standard="ASTM A228",
            shear_modulus_gpa=79.3,
            elastic_modulus_gpa=207.0,
            density_kg_m3=7850.0,
            yield_strength_mpa=1500.0,
            ultimate_strength_mpa=1800.0,
            max_temp_c=150.0,
            corrosion_resistant=False,
            cost_usd_per_kg=3.80,
        )
        mock_db_session.get.return_value = material

        repo = MaterialRepository(db=mock_db_session)
        result = await repo.get_by_id(1)

        assert result is material
        mock_db_session.get.assert_awaited_once_with(SpringMaterial, 1)

    async def test_get_by_id_returns_none(
        self, mock_db_session: AsyncMock
    ) -> None:
        """Verifica que get_by_id retorna None cuando el material no existe."""
        mock_db_session.get.return_value = None

        repo = MaterialRepository(db=mock_db_session)
        result = await repo.get_by_id(999)

        assert result is None

    async def test_get_all_returns_list(
        self, mock_db_session: AsyncMock
    ) -> None:
        """Verifica que get_all retorna la lista completa de materiales.

        Este metodo usa ``result.scalars().all()``, no ``scalar_one_or_none``.
        """
        materials = [
            SpringMaterial(
                id=1,
                name="Mat A",
                standard="ASTM",
                shear_modulus_gpa=79.3,
                elastic_modulus_gpa=207.0,
                density_kg_m3=7850.0,
                yield_strength_mpa=1500.0,
                ultimate_strength_mpa=1800.0,
                max_temp_c=150.0,
                corrosion_resistant=False,
                cost_usd_per_kg=3.80,
            ),
        ]

        # Configurar execute → scalars → all
        scalar_mock = MagicMock()
        scalar_mock.all.return_value = materials
        result_mock = MagicMock()
        result_mock.scalars.return_value = scalar_mock
        mock_db_session.execute.return_value = result_mock

        repo = MaterialRepository(db=mock_db_session)
        result = await repo.get_all()

        assert result == materials
        mock_db_session.execute.assert_awaited_once()

    async def test_get_by_name_found(self, mock_db_session: AsyncMock) -> None:
        """Verifica que get_by_name retorna el material cuando existe."""
        material = SpringMaterial(id=1, name="ASTM A228", standard="ASTM A228")
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = material
        mock_db_session.execute.return_value = result_mock

        repo = MaterialRepository(db=mock_db_session)
        result = await repo.get_by_name("ASTM A228")

        assert result is material

    async def test_get_by_name_not_found(self, mock_db_session: AsyncMock) -> None:
        """Verifica que get_by_name retorna None si no existe (default fixture)."""
        repo = MaterialRepository(db=mock_db_session)
        result = await repo.get_by_name("Nonexistent")

        assert result is None

    async def test_create_adds_and_flushes(
        self, mock_db_session: AsyncMock
    ) -> None:
        """Verifica que create agrega un material y hace flush."""
        repo = MaterialRepository(db=mock_db_session)
        data = {
            "name": "New Alloy",
            "standard": "ASTM X",
            "shear_modulus_gpa": 80.0,
            "elastic_modulus_gpa": 200.0,
            "density_kg_m3": 7800.0,
            "yield_strength_mpa": 1400.0,
            "ultimate_strength_mpa": 1700.0,
            "max_temp_c": 150.0,
            "corrosion_resistant": False,
            "cost_usd_per_kg": 4.5,
        }

        result = await repo.create(data)

        assert isinstance(result, SpringMaterial)
        assert result.name == "New Alloy"
        mock_db_session.add.assert_called_once()
        mock_db_session.flush.assert_awaited_once()

    async def test_update_modifies_existing_fields(
        self, mock_db_session: AsyncMock
    ) -> None:
        """Verifica que update solo modifica los campos provistos."""
        material = SpringMaterial(
            id=1,
            name="Old Name",
            standard="ASTM",
            cost_usd_per_kg=5.0,
            active=True,
        )
        mock_db_session.get.return_value = material

        repo = MaterialRepository(db=mock_db_session)
        result = await repo.update(1, {"cost_usd_per_kg": 6.5})

        assert result is material
        assert material.cost_usd_per_kg == 6.5
        assert material.name == "Old Name"
        mock_db_session.flush.assert_awaited_once()

    async def test_update_returns_none_when_not_found(
        self, mock_db_session: AsyncMock
    ) -> None:
        """Verifica que update retorna None si el material no existe."""
        mock_db_session.get.return_value = None

        repo = MaterialRepository(db=mock_db_session)
        result = await repo.update(999, {"cost_usd_per_kg": 1.0})

        assert result is None
        mock_db_session.flush.assert_not_awaited()

    async def test_deactivate_sets_active_false(
        self, mock_db_session: AsyncMock
    ) -> None:
        """Verifica que deactivate hace soft-delete (active=False) sin borrar la fila."""
        material = SpringMaterial(id=1, name="To Retire", standard="ASTM", active=True)
        mock_db_session.get.return_value = material

        repo = MaterialRepository(db=mock_db_session)
        result = await repo.deactivate(1)

        assert result is material
        assert material.active is False
        mock_db_session.flush.assert_awaited_once()

    async def test_deactivate_returns_none_when_not_found(
        self, mock_db_session: AsyncMock
    ) -> None:
        """Verifica que deactivate retorna None si el material no existe."""
        mock_db_session.get.return_value = None

        repo = MaterialRepository(db=mock_db_session)
        result = await repo.deactivate(999)

        assert result is None

    async def test_list_filtered_applies_filters(
        self, mock_db_session: AsyncMock
    ) -> None:
        """Verifica que list_filtered ejecuta la consulta con los filtros dados."""
        materials = [
            SpringMaterial(id=1, name="Filtered Mat", standard="ASTM", active=True),
        ]
        scalar_mock = MagicMock()
        scalar_mock.all.return_value = materials
        result_mock = MagicMock()
        result_mock.scalars.return_value = scalar_mock
        mock_db_session.execute.return_value = result_mock

        repo = MaterialRepository(db=mock_db_session)
        result = await repo.list_filtered(
            min_operating_temperature_c=100.0,
            corrosion_resistant=True,
            max_cost_usd_per_kg=10.0,
            min_yield_strength_mpa=1000.0,
        )

        assert result == materials
        mock_db_session.execute.assert_awaited_once()


# ─────────────────────────────────────────────────────────────────────────────
# DesignProjectRepository
# ─────────────────────────────────────────────────────────────────────────────

# NOTA: Los metodos de DesignProjectRepository usan
# ``result.scalar_one_or_none()`` DIRECTAMENTE (sin pasar por .scalars()).
# Los mocks deben configurar result_mock.scalar_one_or_none, NO la cadena
# result_mock.scalars().scalar_one_or_none().


class TestDesignProjectRepository:
    """Tests para DesignProjectRepository."""

    async def test_create_returns_project(
        self, mock_db_session: AsyncMock
    ) -> None:
        """Verifica que create agrega un proyecto y hace flush."""
        repo = DesignProjectRepository(db=mock_db_session)
        result = await repo.create(
            session_id="test-session",
            raw_input="Design a compression spring",
            spring_type="compression",
            status="pending",
        )

        assert isinstance(result, DesignProject)
        assert result.session_id == "test-session"
        assert result.raw_user_input == "Design a compression spring"
        assert result.spring_type == "compression"
        assert result.status == "pending"

        mock_db_session.add.assert_called_once()
        mock_db_session.flush.assert_awaited_once()

    async def test_create_uses_defaults(
        self, mock_db_session: AsyncMock
    ) -> None:
        """Verifica que create usa valores por defecto."""
        repo = DesignProjectRepository(db=mock_db_session)
        result = await repo.create(
            session_id="session-2",
            raw_input="I need a spring",
        )

        assert result.spring_type == "unknown"
        assert result.status == "pending"

    async def test_get_by_session_id_found(
        self, mock_db_session: AsyncMock
    ) -> None:
        """Verifica que get_by_session_id retorna el proyecto encontrado."""
        project = DesignProject(
            id=1,
            session_id="test-session",
            raw_user_input="test",
        )

        # Configurar scalar_one_or_none DIRECTAMENTE en el result_mock
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = project
        mock_db_session.execute.return_value = result_mock

        repo = DesignProjectRepository(db=mock_db_session)
        result = await repo.get_by_session_id("test-session")

        assert result is project
        mock_db_session.execute.assert_awaited_once()

    async def test_get_by_session_id_not_found(
        self, mock_db_session: AsyncMock
    ) -> None:
        """Verifica que get_by_session_id retorna None si no existe.

        Usa la configuracion por defecto del fixture: scalar_one_or_none = None.
        """
        repo = DesignProjectRepository(db=mock_db_session)
        result = await repo.get_by_session_id("nonexistent")

        assert result is None

    async def test_update_status_modifies_project(
        self, mock_db_session: AsyncMock
    ) -> None:
        """Verifica que update_status modifica campos y hace flush."""
        project = DesignProject(
            id=1,
            session_id="test-session",
            raw_user_input="test",
            status="pending",
            total_iterations=0,
        )

        # Configurar scalar_one_or_none DIRECTAMENTE
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = project
        mock_db_session.execute.return_value = result_mock

        repo = DesignProjectRepository(db=mock_db_session)
        result = await repo.update_status(
            session_id="test-session",
            status="approved",
            final_report={"status": "approved"},
            total_iterations=3,
        )

        assert result is project
        assert project.status == "approved"
        assert project.final_report == {"status": "approved"}
        assert project.total_iterations == 3
        mock_db_session.flush.assert_awaited_once()

    async def test_update_status_not_found(
        self, mock_db_session: AsyncMock
    ) -> None:
        """Verifica que update_status retorna None si no existe la sesion.

        Usa la configuracion por defecto del fixture.
        """
        repo = DesignProjectRepository(db=mock_db_session)
        result = await repo.update_status(
            session_id="nonexistent", status="approved"
        )

        assert result is None
        mock_db_session.flush.assert_not_awaited()

    async def test_update_status_does_not_clear_report_when_none(
        self, mock_db_session: AsyncMock
    ) -> None:
        """
        Verifica que update_status no sobrescribe el reporte cuando
        final_report es None.
        """
        project = DesignProject(
            id=1,
            session_id="test-session",
            raw_user_input="test",
            final_report={"existing": "data"},
        )

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = project
        mock_db_session.execute.return_value = result_mock

        repo = DesignProjectRepository(db=mock_db_session)
        result = await repo.update_status(
            session_id="test-session",
            status="error",
            total_iterations=1,
        )

        assert result is project
        assert project.final_report == {"existing": "data"}
        assert project.status == "error"

    async def test_update_completed_at_sets_timestamp(
        self, mock_db_session: AsyncMock
    ) -> None:
        """Verifica que update_completed_at asigna la fecha actual UTC."""
        project = DesignProject(
            id=1,
            session_id="test-session",
            raw_user_input="test",
            completed_at=None,
        )

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = project
        mock_db_session.execute.return_value = result_mock

        repo = DesignProjectRepository(db=mock_db_session)
        result = await repo.update_completed_at(session_id="test-session")

        assert result is project
        assert project.completed_at is not None
        assert isinstance(project.completed_at, datetime)
        assert project.completed_at.tzinfo is not None
        mock_db_session.flush.assert_awaited_once()


# ─────────────────────────────────────────────────────────────────────────────
# DesignIterationRepository
# ─────────────────────────────────────────────────────────────────────────────

# NOTA: Los metodos de DesignIterationRepository usan
# ``result.scalars().all()`` (cadena de scalars).


class TestDesignIterationRepository:
    """Tests para DesignIterationRepository."""

    async def test_create_returns_iteration(
        self, mock_db_session: AsyncMock
    ) -> None:
        """Verifica que create agrega una iteracion y hace flush."""
        repo = DesignIterationRepository(db=mock_db_session)
        result = await repo.create(
            project_id=1,
            iteration_number=1,
            geometry_snapshot={"wire_diameter_mm": 3.0},
            compliance_snapshot={"approved": True},
            approved=True,
            failure_modes=[],
            material_id=1,
        )

        assert isinstance(result, DesignIteration)
        assert result.project_id == 1
        assert result.iteration_number == 1
        assert result.geometry_snapshot == {"wire_diameter_mm": 3.0}
        assert result.compliance_snapshot == {"approved": True}
        assert result.approved is True
        assert result.failure_modes == []
        assert result.material_id == 1

        mock_db_session.add.assert_called_once()
        mock_db_session.flush.assert_awaited_once()

    async def test_create_with_minimal_fields(
        self, mock_db_session: AsyncMock
    ) -> None:
        """Verifica que create funciona con solo los campos obligatorios."""
        repo = DesignIterationRepository(db=mock_db_session)
        result = await repo.create(
            project_id=2,
            iteration_number=1,
        )

        assert result.project_id == 2
        assert result.iteration_number == 1
        assert result.geometry_snapshot is None
        assert result.compliance_snapshot is None
        assert result.approved is False
        assert result.failure_modes is None
        assert result.material_id is None

    async def test_get_by_project_returns_list(
        self, mock_db_session: AsyncMock
    ) -> None:
        """Verifica que get_by_project retorna las iteraciones ordenadas.

        Este metodo usa ``result.scalars().all()``.
        """
        iterations = [
            DesignIteration(id=1, project_id=1, iteration_number=1),
            DesignIteration(id=2, project_id=1, iteration_number=2),
        ]

        scalar_mock = MagicMock()
        scalar_mock.all.return_value = iterations
        result_mock = MagicMock()
        result_mock.scalars.return_value = scalar_mock
        mock_db_session.execute.return_value = result_mock

        repo = DesignIterationRepository(db=mock_db_session)
        result = await repo.get_by_project(project_id=1)

        assert result == iterations
        mock_db_session.execute.assert_awaited_once()

    async def test_get_by_project_empty(
        self, mock_db_session: AsyncMock
    ) -> None:
        """Verifica que get_by_project retorna lista vacia."""
        scalar_mock = MagicMock()
        scalar_mock.all.return_value = []
        result_mock = MagicMock()
        result_mock.scalars.return_value = scalar_mock
        mock_db_session.execute.return_value = result_mock

        repo = DesignIterationRepository(db=mock_db_session)
        result = await repo.get_by_project(project_id=999)

        assert result == []
