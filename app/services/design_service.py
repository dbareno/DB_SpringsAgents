"""
app/services/design_service.py
─────────────────────────────────────────────────────────────────────────────
Servicio de diseño que orquesta la interacción entre la API, el grafo de
LangGraph y los repositorios de base de datos.

Phase 3 — multi-turn conversation
──────────────────────────────────
``clarify_design`` no longer concatenates answers into the raw input and
re-runs the ENTIRE graph from ``START``. Instead it resumes the checkpointed
graph (keyed by ``thread_id = session_id``) from the exact ``interrupt()``
call inside ``requirements_analyst_node`` via ``Command(resume=answers)``.
See ``app/core/checkpointer.py`` and ``app/graph/workflow.get_design_graph``.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from fastapi import HTTPException, status
from langgraph.types import Command
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.design_repository import (
    DesignIterationRepository,
    DesignProjectRepository,
)
from app.db.session import get_session_factory
from app.graph.workflow import get_design_graph
from app.schemas.design import DesignResponse, StepProgress
from app.schemas.state import initial_state

logger = logging.getLogger(__name__)

# ── Mapa de pasos a porcentaje de progreso ────────────────────────────────
_STEP_PROGRESS_MAP: dict[str, int] = {
    "requirements_analyst": 10,
    "materials_engineer": 30,
    "design_engineer": 50,
    "normative_inspector": 70,
    "commercial_optimiser": 90,
    "awaiting_clarification": 100,
    "iteration_limit_reached": 100,
    "error_terminal": 100,
}

# ── Cache en memoria de progreso (session_id → StepProgress) ───────────────
# Se escribe durante la ejecución del grafo y se consulta desde el endpoint
# de polling. Los proyectos completados se leen de DB.  Se limpia
# automáticamente cuando el grafo termina.
_status_cache: dict[str, dict[str, Any]] = {}


def _compute_progress_pct(current_step: str | None) -> int:
    """Devuelve el porcentaje de progreso según el paso actual."""
    if current_step is None:
        return 0
    if "redesign" in current_step or current_step == "increment_iteration":
        return 60
    return _STEP_PROGRESS_MAP.get(current_step, 0)


class DesignService:
    """
    Servicio que orquesta el ciclo de vida completo de un diseño de resorte.

    Responsabilidades
    -----------------
    - Crear y actualizar proyectos en la base de datos.
    - Invocar el grafo de LangGraph con streaming de progreso.
    - Extraer iteraciones del estado final y persistirlas.
    - Retornar respuestas listas para la API.
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
        Inicia un nuevo flujo de diseño de resorte de forma asíncrona.

        1. Crea el proyecto en base de datos con estado ``pending``.
        2. Inicia la ejecución del grafo en background.
        3. Retorna inmediatamente con status ``processing``.
        """
        sid = session_id or str(uuid.uuid4())

        # 1. Crear proyecto en estado "pending" (sesión del request)
        await self._project_repo.create(
            session_id=sid,
            raw_input=user_input,
        )
        await self._db.commit()

        # 2. Inicializar cache de progreso
        _status_cache[sid] = {
            "current_step": None,
            "final_state": None,
            "error": None,
        }

        # 3. Iniciar grafo en background con su PROPIA sesión
        asyncio.create_task(
            _run_graph_and_persist(sid, user_input, max_iterations)
        )

        # 4. Retornar inmediatamente con status "processing"
        return DesignResponse(
            session_id=sid,
            status="processing",
            report=None,
            clarification_questions=None,
        )

    async def clarify_design(
        self,
        session_id: str,
        answers: list[str],
    ) -> DesignResponse:
        """
        Reanuda un flujo de diseño luego de que el usuario respondió preguntas.

        Phase 3: en lugar de concatenar las respuestas al input crudo y
        re-ejecutar el grafo completo desde ``START``, esto RESUME el grafo
        checkpointeado desde el punto exacto del ``interrupt()`` (dentro de
        ``requirements_analyst_node``) vía ``Command(resume=answers_dict)``
        sobre el mismo ``thread_id = session_id``. El historial de turnos
        previos persiste en el checkpoint — no se vuelve a ejecutar nada
        aguas arriba del punto de interrupción.

        NOTA: ``requirements_analyst_node`` fusiona estas respuestas con su
        propio ``session_answers`` acumulado (leído de ``state`` al
        re-ejecutarse) — este servicio solo necesita enviar las respuestas
        de ESTE turno, sin leer el checkpoint primero.
        """
        project = await self._project_repo.get_by_session_id(session_id)
        if project is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session '{session_id}' not found.",
            )

        # Recuperar preguntas del último interrupt para emparejar por índice
        # con las respuestas (el frontend sigue enviando answers: string[],
        # en el mismo orden que las preguntas mostradas).
        final_report = project.final_report or {}
        prev_questions: list[str] = final_report.get("clarification_questions", [])
        answers_dict = {
            (prev_questions[i] if i < len(prev_questions) else f"Q{i + 1}"): answer
            for i, answer in enumerate(answers)
        }

        # Inicializar cache de progreso
        _status_cache[session_id] = {
            "current_step": None,
            "final_state": None,
            "error": None,
        }

        # Reanudar el grafo checkpointeado en background con su PROPIA sesión
        asyncio.create_task(
            _resume_graph_and_persist(session_id, answers_dict)
        )

        return DesignResponse(
            session_id=session_id,
            status="processing",
            report=None,
            clarification_questions=None,
        )

    async def get_design(self, session_id: str) -> DesignResponse | None:
        """Recupera un diseño completo desde la base de datos."""
        project = await self._project_repo.get_by_session_id(session_id)
        if project is None:
            return None
        return _project_to_response(project)

    async def get_step_progress(self, session_id: str) -> StepProgress | None:
        """
        Retorna el progreso actual de un diseño en ejecución.
        """
        # 1. Buscar en cache de progreso (procesando o recién terminado)
        cached = _status_cache.get(session_id)
        if cached is not None:
            final_state = cached.get("final_state")
            error = cached.get("error")

            if final_state is not None:
                report = final_state.get("final_report") or {}
                graph_status = report.get("status", "completed")
                step = final_state.get("current_step")
                del _status_cache[session_id]
                return StepProgress(
                    session_id=session_id,
                    status=graph_status,
                    current_step=step,
                    progress_pct=100,
                )
            if error is not None:
                del _status_cache[session_id]
                return StepProgress(
                    session_id=session_id,
                    status="error",
                    current_step=None,
                    progress_pct=0,
                    error=error,
                )

            current_step = cached.get("current_step")
            return StepProgress(
                session_id=session_id,
                status="processing",
                current_step=current_step,
                progress_pct=_compute_progress_pct(current_step),
            )

        # 2. Buscar en DB (diseño completado)
        project = await self._project_repo.get_by_session_id(session_id)
        if project is None:
            return None

        return StepProgress(
            session_id=session_id,
            status=project.status or "completed",
            current_step=None,
            progress_pct=100,
        )


# ── Funciones de ejecución en background ─────────────────────────────────


def _make_config(session_id: str) -> dict[str, Any]:
    """Config de LangGraph con thread_id = session_id (clave del checkpoint)."""
    return {"configurable": {"thread_id": session_id}}


def _extract_interrupt_questions(result: dict[str, Any]) -> list[str] | None:
    """
    Si ``result`` contiene un ``__interrupt__`` (el grafo se pausó dentro de
    ``requirements_analyst_node``), retorna la lista de preguntas de
    clarificación del payload del interrupt. Retorna ``None`` si el grafo
    terminó normalmente (sin pausas).
    """
    interrupts = result.get("__interrupt__")
    if not interrupts:
        return None
    # Un solo interrupt por nodo en este grafo (requirements_analyst_node
    # llama interrupt() una vez por ronda) — tomar el primero.
    payload = interrupts[0].value
    if isinstance(payload, dict):
        return list(payload.get("questions", []))
    return []


async def _stream_graph(
    session_id: str,
    graph_input: Any,
    config: dict[str, Any],
) -> dict[str, Any]:
    """
    Corre ``graph.astream(graph_input, config, stream_mode="values")``,
    actualizando ``_status_cache[session_id]["current_step"]`` en cada paso
    (para el polling de progreso), y retorna el ÚLTIMO valor emitido —
    que incluye ``__interrupt__`` si el grafo se pausó.
    """
    graph = await get_design_graph()
    final_state: dict[str, Any] = {}
    async for event in graph.astream(graph_input, config, stream_mode="values"):
        if not isinstance(event, dict):
            continue
        current_step = event.get("current_step")
        if current_step:
            cached = _status_cache.get(session_id)
            if cached is not None:
                cached["current_step"] = current_step
        final_state = event
    return final_state


async def _run_graph_and_persist(
    session_id: str,
    user_input: str,
    max_iterations: int = 5,
) -> None:
    """
    Ejecuta el grafo de LangGraph (checkpointeado) desde ``START`` y persiste
    los resultados al terminar — o el estado ``needs_clarification`` si el
    grafo se pausó en un ``interrupt()``.

    Corre como tarea asíncrona en background (asyncio.create_task).
    Crea su PROPIA sesión de base de datos porque la del request
    ya se cerró cuando el endpoint retornó.
    """
    state = initial_state(user_input, max_iterations=max_iterations)
    config = _make_config(session_id)

    try:
        result = await _stream_graph(session_id, state, config)
        await _persist_graph_result(session_id, result)
    except Exception as exc:
        await _persist_graph_error(session_id, exc)


async def _resume_graph_and_persist(
    session_id: str,
    answers: dict[str, str],
) -> None:
    """
    RESUME el grafo checkpointeado desde el punto exacto de ``interrupt()``
    usando ``Command(resume=answers)`` sobre el mismo ``thread_id``.

    ``requirements_analyst_node`` recibe ``answers`` como el valor de
    retorno de su llamada a ``interrupt()`` y las fusiona con su propio
    ``session_answers`` acumulado — este servicio no necesita leer el
    checkpoint primero.

    Reemplaza el viejo patrón de concatenar respuestas al input crudo y
    re-ejecutar el grafo completo desde ``START`` (Phase 3).
    """
    config = _make_config(session_id)

    try:
        result = await _stream_graph(session_id, Command(resume=answers), config)
        await _persist_graph_result(session_id, result)
    except Exception as exc:
        await _persist_graph_error(session_id, exc)


async def _persist_graph_result(session_id: str, result: dict[str, Any]) -> None:
    """
    Persiste el resultado de una invocación (o resume) del grafo.

    Si ``result`` contiene ``__interrupt__``, el grafo está PAUSADO esperando
    respuestas — se persiste como ``needs_clarification`` con las preguntas
    del interrupt (NO se llama a _finalize_project/_save_iterations, porque
    no hay ``final_report`` real todavía; el checkpoint conserva el estado
    completo para el próximo resume).
    """
    interrupt_questions = _extract_interrupt_questions(result)

    factory = await get_session_factory()
    async with factory() as db:
        project_repo = DesignProjectRepository(db)
        iteration_repo = DesignIterationRepository(db)

        if interrupt_questions is not None:
            partial_requirements = (
                result["__interrupt__"][0].value.get("partial_requirements", {})
                if isinstance(result["__interrupt__"][0].value, dict)
                else {}
            )
            await project_repo.update_status(
                session_id=session_id,
                status="needs_clarification",
                final_report={
                    "status": "needs_clarification",
                    "clarification_questions": interrupt_questions,
                    "partial_requirements": partial_requirements,
                },
                total_iterations=result.get("iteration_count", 0),
            )
        else:
            await _save_iterations(project_repo, iteration_repo, session_id, result)
            await _finalize_project(project_repo, session_id, result)

        await db.commit()

    # Actualizar cache de progreso para que el polling detecte el fin del run.
    cached = _status_cache.get(session_id)
    if cached is not None:
        cached["final_state"] = result

    logger.info(
        "[DesignService] Graph run finished for %s. Interrupted=%s, status=%s",
        session_id,
        interrupt_questions is not None,
        (result.get("final_report") or {}).get(
            "status", "needs_clarification" if interrupt_questions else "unknown"
        ),
    )


async def _persist_graph_error(session_id: str, exc: Exception) -> None:
    """Persiste un error de ejecución del grafo (invoke o resume)."""
    logger.error(
        "[DesignService] Graph execution failed for %s: %s",
        session_id,
        exc,
    )
    cached = _status_cache.get(session_id)
    if cached is not None:
        cached["error"] = str(exc)

    try:
        factory = await get_session_factory()
        async with factory() as db:
            project_repo = DesignProjectRepository(db)
            await project_repo.update_status(
                session_id=session_id,
                status="error",
                final_report={"status": "error", "message": str(exc)},
            )
            await db.commit()
    except Exception as db_exc:
        logger.error(
            "[DesignService] DB update also failed for %s: %s",
            session_id,
            db_exc,
        )


async def _save_iterations(
    project_repo: DesignProjectRepository,
    iteration_repo: DesignIterationRepository,
    session_id: str,
    final_state: dict[str, Any],
) -> None:
    """Extrae iteraciones del estado final y las persiste."""
    project = await project_repo.get_by_session_id(session_id)
    if project is None:
        logger.warning(
            "[DesignService] Project %s not found for iteration save.",
            session_id,
        )
        return

    compliance = final_state.get("compliance")
    geometry = final_state.get("geometry")

    if geometry is None and compliance is None:
        return

    material = final_state.get("material")
    iteration_count = final_state.get("iteration_count", 0)

    await iteration_repo.create(
        project_id=project.id,
        iteration_number=max(iteration_count, 1),
        geometry_snapshot=_serialize(geometry) if geometry else None,
        compliance_snapshot=_serialize(compliance) if compliance else None,
        approved=compliance.approved if compliance else False,
        failure_modes=compliance.failure_modes if compliance else None,
        material_id=material.material_id if material else None,
    )


async def _finalize_project(
    project_repo: DesignProjectRepository,
    session_id: str,
    final_state: dict[str, Any],
) -> None:
    """Actualiza el proyecto en DB con el estado final del grafo."""
    final_report: dict[str, Any] = final_state.get("final_report") or {}
    graph_status = final_report.get("status", "unknown")

    status_map: dict[str, str] = {
        "approved": "approved",
        "needs_clarification": "needs_clarification",
        "iteration_limit_reached": "iteration_limit_reached",
        "error": "error",
    }
    api_status = status_map.get(graph_status, graph_status)
    iteration_count = final_state.get("iteration_count", 0)

    await project_repo.update_status(
        session_id=session_id,
        status=api_status,
        final_report=final_report,
        total_iterations=iteration_count,
    )

    if api_status in ("approved", "error", "iteration_limit_reached"):
        await project_repo.update_completed_at(session_id=session_id)


def _project_to_response(project: Any) -> DesignResponse:
    """Convierte un proyecto ORM a DesignResponse."""
    final_report = project.final_report or {}
    api_status = project.status

    return DesignResponse(
        session_id=project.session_id,
        status=api_status,
        report=final_report if api_status in ("approved", "iteration_limit_reached") else None,
        clarification_questions=(
            final_report.get("clarification_questions")
            if api_status == "needs_clarification"
            else None
        ),
        errors=final_report.get("errors") if api_status == "error" else None,
    )


def _serialize(obj: Any) -> dict[str, Any]:
    """Serializa un objeto Pydantic a dict."""
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if isinstance(obj, dict):
        return obj
    return {}
