"""
app/services/design_service.py
─────────────────────────────────────────────────────────────────────────────
Servicio de diseño que orquesta la interacción entre la API, el grafo de
LangGraph y los repositorios de base de datos.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.design_repository import (
    DesignIterationRepository,
    DesignProjectRepository,
)
from app.graph.workflow import spring_design_graph
from app.schemas.design import DesignResponse
from app.schemas.state import initial_state


class DesignService:
    """
    Servicio que orquesta el ciclo de vida completo de un diseño de resorte.

    Responsabilidades
    -----------------
    - Crear y actualizar proyectos en la base de datos.
    - Invocar el grafo de LangGraph de forma asíncrona no bloqueante
      (vía ``asyncio.to_thread`` / ``run_in_executor``).
    - Extraer iteraciones del estado final y persistirlas.
    - Retornar respuestas listas para la API.

    Uso
    ---
    >>> service = DesignService(db=async_session)
    >>> response = await service.start_design(
    ...     user_input="I need a compression spring...",
    ...     max_iterations=5,
    ... )
    """

    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._project_repo = DesignProjectRepository(db)
        self._iteration_repo = DesignIterationRepository(db)

    async def start_design(
        self,
        user_input: str,
        max_iterations: int = 5,
        session_id: str | None = None,
    ) -> DesignResponse:
        """
        Inicia un nuevo flujo de diseño de resorte.

        1. Crea el proyecto en base de datos con estado ``pending``.
        2. Invoca el grafo de LangGraph en un executor separado.
        3. Persiste las iteraciones extraídas del estado final.
        4. Actualiza el proyecto con los resultados y retorna la respuesta.
        """
        sid = session_id or str(uuid.uuid4())

        # 1. Crear proyecto en estado "pending"
        project = await self._project_repo.create(
            session_id=sid,
            raw_input=user_input,
        )

        # 2. Invocar el grafo de forma no bloqueante
        state = initial_state(user_input, max_iterations=max_iterations)
        final_state = await self._run_graph(state, sid)

        # 3. Guardar iteraciones en base de datos
        await self._save_iterations(project.id, final_state)

        # 4. Finalizar y retornar respuesta
        return await self._finalize_project(sid, final_state)

    async def clarify_design(
        self,
        session_id: str,
        answers: str,
    ) -> DesignResponse:
        """
        Reanuda un flujo de diseño luego de que el usuario respondió preguntas.

        1. Carga el proyecto original desde la base de datos.
        2. Combina el input original con las respuestas del usuario.
        3. Re-invoca el grafo.
        4. Persiste las nuevas iteraciones y actualiza el proyecto.
        """
        project = await self._project_repo.get_by_session_id(session_id)
        if project is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session '{session_id}' not found.",
            )

        combined_input = (
            f"{project.raw_user_input}\n\nAdditional information: {answers}"
        )

        state = initial_state(combined_input)
        final_state = await self._run_graph(state, session_id)

        await self._save_iterations(project.id, final_state)
        return await self._finalize_project(session_id, final_state)

    async def get_design(self, session_id: str) -> DesignResponse | None:
        """
        Recupera un diseño completo desde la base de datos.

        Args:
            session_id: Identificador de sesión a buscar.

        Returns:
            DesignResponse si existe la sesión, None en caso contrario.
        """
        project = await self._project_repo.get_by_session_id(session_id)
        if project is None:
            return None
        return self._project_to_response(project)

    # ── Private helpers ───────────────────────────────────────────────────

    async def _run_graph(
        self,
        state: dict[str, Any],
        session_id: str,
    ) -> dict[str, Any]:
        """
        Ejecuta el grafo de LangGraph en un executor de hilos para no
        bloquear el event loop de asyncio.

        Si el grafo falla, marca el proyecto como ``error`` y relanza
        la excepción como HTTPException.
        """
        loop = asyncio.get_event_loop()
        try:
            return await loop.run_in_executor(
                None, spring_design_graph.invoke, state
            )
        except Exception as exc:
            await self._project_repo.update_status(
                session_id=session_id,
                status="error",
                final_report={"status": "error", "message": str(exc)},
            )
            await self._db.commit()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Graph execution failed: {exc}",
            ) from exc

    async def _save_iterations(
        self,
        project_id: int,
        final_state: dict[str, Any],
    ) -> None:
        """
        Extrae los datos de iteraciones del estado final de LangGraph
        y los persiste en la base de datos.

        Solo guarda una iteración si hay datos de geometría o compliance
        disponibles (omite estados de clarificación sin diseño).
        """
        compliance = final_state.get("compliance")
        geometry = final_state.get("geometry")

        # No hay datos de diseño que persistir (ej. estado de clarificación)
        if geometry is None and compliance is None:
            return

        material = final_state.get("material")
        iteration_count = final_state.get("iteration_count", 0)

        await self._iteration_repo.create(
            project_id=project_id,
            iteration_number=max(iteration_count, 1),
            geometry_snapshot=(
                self._serialize(geometry) if geometry else None
            ),
            compliance_snapshot=(
                self._serialize(compliance) if compliance else None
            ),
            approved=compliance.approved if compliance else False,
            failure_modes=compliance.failure_modes if compliance else None,
            material_id=material.material_id if material else None,
        )

    async def _finalize_project(
        self,
        session_id: str,
        final_state: dict[str, Any],
    ) -> DesignResponse:
        """
        Toma el estado final del grafo, actualiza el proyecto en DB
        y construye un DesignResponse.
        """
        final_report: dict[str, Any] = final_state.get("final_report") or {}
        graph_status = final_report.get("status", "unknown")

        # Mapa de estados internos del grafo a estados de la API
        status_map: dict[str, str] = {
            "approved": "approved",
            "needs_clarification": "needs_clarification",
            "iteration_limit_reached": "iteration_limit_reached",
            "error": "error",
        }
        api_status = status_map.get(graph_status, graph_status)

        iteration_count = final_state.get("iteration_count", 0)

        await self._project_repo.update_status(
            session_id=session_id,
            status=api_status,
            final_report=final_report,
            total_iterations=iteration_count,
        )

        # Marcar completed_at solo para estados terminales
        if api_status in ("approved", "error", "iteration_limit_reached"):
            await self._project_repo.update_completed_at(
                session_id=session_id
            )

        await self._db.commit()

        return DesignResponse(
            session_id=session_id,
            status=api_status,
            report=final_report if api_status == "approved" else None,
            clarification_questions=(
                final_report.get("clarification_questions")
                if api_status == "needs_clarification"
                else None
            ),
            errors=final_state.get("errors") or None,
        )

    def _project_to_response(self, project: Any) -> DesignResponse:
        """
        Convierte un DesignProject (ORM) a un DesignResponse (Pydantic).

        Args:
            project: Instancia de DesignProject desde SQLAlchemy.

        Returns:
            DesignResponse listo para la respuesta HTTP.
        """
        final_report = project.final_report or {}
        api_status = project.status

        return DesignResponse(
            session_id=project.session_id,
            status=api_status,
            report=final_report if api_status == "approved" else None,
            clarification_questions=(
                final_report.get("clarification_questions")
                if api_status == "needs_clarification"
                else None
            ),
            errors=final_report.get("errors")
            if api_status == "error"
            else None,
        )

    @staticmethod
    def _serialize(obj: Any) -> dict[str, Any]:
        """
        Serializa un objeto Pydantic (o cualquier objeto con ``model_dump``)
        a un dict. Si el objeto no tiene ``model_dump``, retorna un dict vacío.
        """
        if hasattr(obj, "model_dump"):
            return obj.model_dump()
        if isinstance(obj, dict):
            return obj
        return {}
