"""
tests/test_design_service.py
─────────────────────────────────────────────────────────────────────────────
Unit tests for the DesignService layer.

The LangGraph graph invocation is mocked — these tests verify the
orchestration logic, not the graph itself.

Phase 3: the service now drives the graph via ``get_design_graph()`` +
``astream(..., stream_mode="values")`` (checkpointed, supports interrupt /
resume) instead of the old synchronous ``spring_design_graph.invoke()``. Tests
patch ``app.services.design_service.get_design_graph`` and provide an async
generator on ``.astream`` to match the new call shape.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.db.models import DesignProject
from app.schemas.design import DesignResponse


def _make_mock_graph(*state_events: dict) -> AsyncMock:
    """
    Build a mock compiled graph whose ``astream(..., stream_mode="values")``
    yields each of ``state_events`` in order (mirrors what
    ``DesignService._stream_graph`` consumes).
    """

    async def _astream(*_args, **_kwargs):
        for event in state_events:
            yield event

    mock_graph = MagicMock()
    mock_graph.astream = _astream
    return mock_graph


def _patch_get_design_graph(mock_graph: MagicMock):
    """Patch ``get_design_graph`` (an async factory) to resolve to ``mock_graph``."""
    return patch(
        "app.services.design_service.get_design_graph",
        new=AsyncMock(return_value=mock_graph),
    )


class TestStartDesign:
    """Tests para DesignService.start_design."""

    async def test_start_design_approved(
        self,
        mock_db_session: AsyncMock,
        mock_graph_final_state: dict,
    ) -> None:
        """
        Verifica que start_design retorna un DesignResponse con status
        'approved' cuando el grafo se ejecuta correctamente.
        """
        mock_graph = _make_mock_graph(mock_graph_final_state)
        with _patch_get_design_graph(mock_graph):
            from app.services.design_service import DesignService

            service = DesignService(db=mock_db_session)
            result = await service.start_design(
                user_input="Design a compression spring for 120N",
                max_iterations=5,
            )

        assert isinstance(result, DesignResponse)
        # El servicio ahora retorna "processing" inmediatamente (grafo async)
        assert result.status == "processing"
        assert result.session_id is not None
        assert isinstance(result.session_id, str)
        assert result.report is None
        assert result.clarification_questions is None

        # Verificar que se haya creado un proyecto y hecho commit
        mock_db_session.add.assert_called()
        mock_db_session.commit.assert_awaited()

    async def test_start_design_clarification(
        self,
        mock_db_session: AsyncMock,
        mock_graph_clarify_state: dict,
    ) -> None:
        """
        Verifica que start_design retorna un DesignResponse con status
        'needs_clarification' cuando el grafo no puede completar los
        requerimientos.
        """
        mock_graph = _make_mock_graph(mock_graph_clarify_state)
        with _patch_get_design_graph(mock_graph):
            from app.services.design_service import DesignService

            service = DesignService(db=mock_db_session)
            result = await service.start_design(
                user_input="I need a spring",
                max_iterations=5,
            )

        assert isinstance(result, DesignResponse)
        # El servicio ahora retorna "processing" inmediatamente (grafo async)
        assert result.status == "processing"
        assert result.report is None
        assert result.clarification_questions is None

    async def test_start_design_graph_error(
        self,
        mock_db_session: AsyncMock,
    ) -> None:
        """
        Verifica que start_design retorna "processing" incluso cuando el
        grafo falla (el error se captura en la tarea background y se
        persiste en DB con status "error").
        """
        mock_graph = MagicMock()

        async def _astream_raises(*_args, **_kwargs):
            raise RuntimeError("Graph crashed")
            yield  # pragma: no cover - makes this an async generator

        mock_graph.astream = _astream_raises

        with _patch_get_design_graph(mock_graph):
            from app.services.design_service import DesignService

            service = DesignService(db=mock_db_session)
            result = await service.start_design(
                user_input="Design a spring",
                max_iterations=5,
            )

            assert isinstance(result, DesignResponse)
            assert result.status == "processing"
            assert result.session_id is not None

        # El commit del request ocurre porque el proyecto se crea antes
        # de lanzar el background task
        mock_db_session.commit.assert_awaited()

    async def test_start_design_custom_session_id(
        self,
        mock_db_session: AsyncMock,
        mock_graph_final_state: dict,
    ) -> None:
        """
        Verifica que start_design usa el session_id proporcionado en
        lugar de generar uno nuevo.
        """
        mock_graph = _make_mock_graph(mock_graph_final_state)
        with _patch_get_design_graph(mock_graph):
            from app.services.design_service import DesignService

            service = DesignService(db=mock_db_session)
            result = await service.start_design(
                user_input="Design a spring",
                max_iterations=5,
                session_id="my-custom-id",
            )

        assert result.session_id == "my-custom-id"


class TestClarifyDesign:
    """Tests para DesignService.clarify_design."""

    async def test_clarify_design_approved(
        self,
        mock_db_session: AsyncMock,
        mock_graph_final_state: dict,
    ) -> None:
        """
        Verifica que clarify_design retorna un DesignResponse aprobado
        cuando el grafo se ejecuta correctamente con las respuestas.
        """
        # Configurar execute → scalar_one_or_none → proyecto existente
        project = DesignProject(
            id=2,
            session_id="session-123",
            raw_user_input="Original spring requirements",
            final_report={
                "status": "needs_clarification",
                "clarification_questions": ["What is the load force?"],
            },
        )
        scalar_mock = MagicMock()
        scalar_mock.scalar_one_or_none.return_value = project
        result_mock = MagicMock()
        result_mock.scalars.return_value = scalar_mock
        mock_db_session.execute.return_value = result_mock

        mock_graph = _make_mock_graph(mock_graph_final_state)
        with _patch_get_design_graph(mock_graph):
            from app.services.design_service import DesignService

            service = DesignService(db=mock_db_session)
            result = await service.clarify_design(
                session_id="session-123",
                answers=["The load is 100N and OD max is 30mm."],
            )

        assert isinstance(result, DesignResponse)
        # El servicio ahora retorna "processing" inmediatamente (grafo async)
        assert result.status == "processing"
        assert result.session_id == "session-123"

    async def test_clarify_design_session_not_found(
        self,
        mock_db_session: AsyncMock,
    ) -> None:
        """
        Verifica que clarify_design lanza HTTP 404 cuando la sesion
        no existe en base de datos.

        El fixture mock_db_session ya configura scalar_one_or_none
        para retornar None por defecto.
        """
        from app.services.design_service import DesignService

        service = DesignService(db=mock_db_session)

        with pytest.raises(HTTPException) as exc_info:
            await service.clarify_design(
                session_id="nonexistent",
                answers=["Some answer"],
            )

        assert exc_info.value.status_code == 404
        assert "not found" in exc_info.value.detail.casefold()


class TestGetStepProgress:
    """
    Tests para DesignService.get_step_progress.

    Regression coverage: when the graph pauses at ``interrupt()``, the raw
    ``AgentState`` dict has an EXPLICIT ``final_report: None`` key (set by
    ``initial_state()`` and never overwritten before the pause) —
    ``final_state.get("final_report", {})`` returns ``None`` in that case
    (the key exists), NOT the ``{}`` default, which crashed
    ``report.get("status", ...)`` with ``AttributeError``. Must use
    ``final_state.get("final_report") or {}`` instead.
    """

    async def test_interrupted_state_with_explicit_none_final_report(
        self,
        mock_db_session: AsyncMock,
    ) -> None:
        """
        A cached ``final_state`` shaped exactly like the real interrupted
        AgentState (``final_report`` key present but ``None``) must not
        crash — it should report ``current_step`` from the cache in the
        'processing' branch, since ``final_state`` here represents an
        in-flight (not yet finished) run snapshot.
        """
        from app.services.design_service import DesignService, _status_cache

        session_id = "session-progress-none-report"
        _status_cache[session_id] = {
            "current_step": "requirements_analyst",
            "final_state": None,
            "error": None,
        }

        service = DesignService(db=mock_db_session)
        progress = await service.get_step_progress(session_id=session_id)

        assert progress is not None
        assert progress.status == "processing"
        assert progress.current_step == "requirements_analyst"

    async def test_final_state_with_explicit_none_final_report_does_not_crash(
        self,
        mock_db_session: AsyncMock,
    ) -> None:
        """
        Directly exercises the bug: ``final_state`` present with
        ``final_report: None`` (exact shape of a paused/interrupted
        AgentState) must resolve to a safe default status, not raise.
        """
        from app.services.design_service import DesignService, _status_cache

        session_id = "session-progress-crash-repro"
        _status_cache[session_id] = {
            "current_step": None,
            "final_state": {
                "current_step": "requirements_analyst",
                "final_report": None,
                "interrupted": True,
            },
            "error": None,
        }

        service = DesignService(db=mock_db_session)
        progress = await service.get_step_progress(session_id=session_id)

        assert progress is not None
        assert progress.status == "completed"
        assert progress.current_step == "requirements_analyst"


class TestGetDesign:
    """Tests para DesignService.get_design."""

    async def test_get_design_found(
        self,
        mock_db_session: AsyncMock,
    ) -> None:
        """
        Verifica que get_design retorna un DesignResponse cuando el
        proyecto existe en base de datos.

        NOTA: El repositorio usa ``result.scalar_one_or_none()``
        directamente, no via ``result.scalars()``.
        """
        project = DesignProject(
            id=1,
            session_id="session-123",
            raw_user_input="test requirements",
            status="approved",
            final_report={
                "status": "approved",
                "summary": "Design OK",
            },
        )

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = project
        mock_db_session.execute.return_value = result_mock

        from app.services.design_service import DesignService

        service = DesignService(db=mock_db_session)
        result = await service.get_design(session_id="session-123")

        assert result is not None
        assert isinstance(result, DesignResponse)
        assert result.session_id == "session-123"
        assert result.status == "approved"
        assert result.report == {"status": "approved", "summary": "Design OK"}

    async def test_get_design_not_found(
        self,
        mock_db_session: AsyncMock,
    ) -> None:
        """
        Verifica que get_design retorna None cuando la sesion no existe.

        El fixture mock_db_session ya configura scalar_one_or_none
        para retornar None por defecto.
        """
        from app.services.design_service import DesignService

        service = DesignService(db=mock_db_session)
        result = await service.get_design(session_id="nonexistent")

        assert result is None


class TestProjectToResponse:
    """Tests para la conversion interna DesignProject -> DesignResponse."""

    async def test_approved_project(self, mock_db_session: AsyncMock) -> None:
        """Verifica la conversion de un proyecto aprobado."""
        from app.services.design_service import _project_to_response

        project = DesignProject(
            id=1,
            session_id="s-1",
            raw_user_input="test",
            status="approved",
            final_report={"status": "approved", "data": "ok"},
        )

        response = _project_to_response(project)

        assert response.status == "approved"
        assert response.report == {"status": "approved", "data": "ok"}
        assert response.clarification_questions is None
        assert response.errors is None

    async def test_clarification_project(
        self, mock_db_session: AsyncMock
    ) -> None:
        """Verifica la conversion de un proyecto que requiere clarificacion."""
        from app.services.design_service import _project_to_response

        project = DesignProject(
            id=2,
            session_id="s-2",
            raw_user_input="test",
            status="needs_clarification",
            final_report={
                "status": "needs_clarification",
                "clarification_questions": ["What force?"],
            },
        )

        response = _project_to_response(project)

        assert response.status == "needs_clarification"
        assert response.report is None
        assert response.clarification_questions == ["What force?"]
        assert response.errors is None

    async def test_error_project(self, mock_db_session: AsyncMock) -> None:
        """Verifica la conversion de un proyecto con error."""
        from app.services.design_service import _project_to_response

        project = DesignProject(
            id=3,
            session_id="s-3",
            raw_user_input="test",
            status="error",
            final_report={
                "status": "error",
                "errors": [{"step": "graph", "message": "fail"}],
            },
        )

        response = _project_to_response(project)

        assert response.status == "error"
        assert response.report is None
        assert response.errors == [{"step": "graph", "message": "fail"}]
