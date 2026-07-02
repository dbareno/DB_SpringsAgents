"""
app/db/repositories/design_repository.py
─────────────────────────────────────────────────────────────────────────────
Repositorios para las entidades DesignProject y DesignIteration.

Encapsulan todas las consultas a las tablas ``design_projects`` e
``design_iterations``.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DesignIteration, DesignProject


class DesignProjectRepository:
    """
    Repositorio para operaciones CRUD sobre proyectos de diseño.

    Cada proyecto representa una invocación completa al pipeline de diseño.
    """

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create(
        self,
        session_id: str,
        raw_input: str,
        spring_type: str = "unknown",
        status: str = "pending",
    ) -> DesignProject:
        """
        Crea un nuevo proyecto de diseño y lo persiste en base de datos.

        Args:
            session_id: Identificador único de sesión.
            raw_input: Texto original ingresado por el usuario.
            spring_type: Tipo de resorte detectado.
            status: Estado inicial del proyecto.

        Returns:
            La instancia de DesignProject creada (con ID asignado tras flush).
        """
        project = DesignProject(
            session_id=session_id,
            raw_user_input=raw_input,
            spring_type=spring_type,
            status=status,
        )
        self._db.add(project)
        await self._db.flush()
        return project

    async def get_by_session_id(self, session_id: str) -> DesignProject | None:
        """
        Retorna el proyecto más reciente asociado a un session_id.

        Args:
            session_id: Identificador de sesión a buscar.

        Returns:
            DesignProject si existe, None en caso contrario.
        """
        result = await self._db.execute(
            select(DesignProject)
            .where(DesignProject.session_id == session_id)
            .order_by(DesignProject.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def update_status(
        self,
        session_id: str,
        status: str,
        final_report: dict | None = None,
        total_iterations: int = 0,
    ) -> DesignProject | None:
        """
        Actualiza el estado, reporte final y conteo de iteraciones.

        Args:
            session_id: Identificador de sesión del proyecto.
            status: Nuevo estado del proyecto.
            final_report: Reporte final estructurado (opcional).
            total_iterations: Número total de iteraciones ejecutadas.

        Returns:
            DesignProject actualizado, o None si no se encontró la sesión.
        """
        project = await self.get_by_session_id(session_id)
        if project is None:
            return None
        project.status = status
        if final_report is not None:
            project.final_report = final_report
        project.total_iterations = total_iterations
        await self._db.flush()
        return project

    async def update_completed_at(
        self,
        session_id: str,
    ) -> DesignProject | None:
        """
        Marca la fecha de finalización del proyecto con la hora actual UTC.

        Args:
            session_id: Identificador de sesión del proyecto.

        Returns:
            DesignProject actualizado, o None si no se encontró la sesión.
        """
        project = await self.get_by_session_id(session_id)
        if project is None:
            return None
        project.completed_at = datetime.now(timezone.utc)
        await self._db.flush()
        return project


class DesignIterationRepository:
    """
    Repositorio para operaciones CRUD sobre iteraciones de diseño.

    Cada iteración representa un ciclo completo de diseño geométrico +
    verificación de cumplimiento normativo.
    """

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create(
        self,
        project_id: int,
        iteration_number: int,
        geometry_snapshot: dict | None = None,
        compliance_snapshot: dict | None = None,
        approved: bool = False,
        failure_modes: list | None = None,
        material_id: int | None = None,
    ) -> DesignIteration:
        """
        Crea una nueva iteración de diseño y la persiste.

        Args:
            project_id: ID del proyecto al que pertenece.
            iteration_number: Número de iteración (1-based).
            geometry_snapshot: Datos de geometría calculados.
            compliance_snapshot: Resultado de verificación normativa.
            approved: Si la iteración fue aprobada.
            failure_modes: Lista de modos de falla detectados.
            material_id: ID del material seleccionado (opcional).

        Returns:
            La instancia de DesignIteration creada.
        """
        iteration = DesignIteration(
            project_id=project_id,
            iteration_number=iteration_number,
            geometry_snapshot=geometry_snapshot,
            compliance_snapshot=compliance_snapshot,
            approved=approved,
            failure_modes=failure_modes,
            material_id=material_id,
        )
        self._db.add(iteration)
        await self._db.flush()
        return iteration

    async def get_by_project(
        self,
        project_id: int,
    ) -> list[DesignIteration]:
        """
        Retorna todas las iteraciones de un proyecto ordenadas por número.

        Args:
            project_id: ID del proyecto.

        Returns:
            Lista de iteraciones del proyecto.
        """
        result = await self._db.execute(
            select(DesignIteration)
            .where(DesignIteration.project_id == project_id)
            .order_by(DesignIteration.iteration_number)
        )
        return list(result.scalars().all())
