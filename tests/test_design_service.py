"""
tests/test_design_service.py
─────────────────────────────────────────────────────────────────────────────
Unit tests for the DesignService layer.

The LangGraph graph invocation is mocked — these tests verify the
orchestration logic, not the graph itself.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.db.models import DesignProject
from app.schemas.design import DesignResponse


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
        with patch(
            "app.services.design_service.spring_design_graph"
        ) as mock_graph:
            mock_graph.invoke.return_value = mock_graph_final_state

            from app.services.design_service import DesignService

            service = DesignService(db=mock_db_session)
            result = await service.start_design(
                user_input="Design a compression spring for 120N",
                max_iterations=5,
            )

        assert isinstance(result, DesignResponse)
        assert result.status == "approved"
        assert result.session_id is not None
        assert isinstance(result.session_id, str)
        assert result.report is not None
        assert result.report["status"] == "approved"
        assert result.clarification_questions is None

        # Verificar que se haya creado un proyecto y hecho commit
        mock_db_session.add.assert_called()
        mock_db_session.flush.assert_awaited()
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
        with patch(
            "app.services.design_service.spring_design_graph"
        ) as mock_graph:
            mock_graph.invoke.return_value = mock_graph_clarify_state

            from app.services.design_service import DesignService

            service = DesignService(db=mock_db_session)
            result = await service.start_design(
                user_input="I need a spring",
                max_iterations=5,
            )

        assert isinstance(result, DesignResponse)
        assert result.status == "needs_clarification"
        assert result.report is None
        assert result.clarification_questions is not None
        assert len(result.clarification_questions) == 2

    async def test_start_design_graph_error(
        self,
        mock_db_session: AsyncMock,
    ) -> None:
        """
        Verifica que start_design relanza el error como HTTP 500 cuando
        el grafo falla.
        """
        with patch(
            "app.services.design_service.spring_design_graph"
        ) as mock_graph:
            mock_graph.invoke.side_effect = RuntimeError("Graph crashed")

            from app.services.design_service import DesignService

            service = DesignService(db=mock_db_session)

            with pytest.raises(HTTPException) as exc_info:
                await service.start_design(
                    user_input="Design a spring",
                    max_iterations=5,
                )

            assert exc_info.value.status_code == 500
            assert "Graph execution failed" in exc_info.value.detail

        # Verificar que se haya intentado commit (para marcar error)
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
        with patch(
            "app.services.design_service.spring_design_graph"
        ) as mock_graph:
            mock_graph.invoke.return_value = mock_graph_final_state

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
        )
        scalar_mock = MagicMock()
        scalar_mock.scalar_one_or_none.return_value = project
        result_mock = MagicMock()
        result_mock.scalars.return_value = scalar_mock
        mock_db_session.execute.return_value = result_mock

        with patch(
            "app.services.design_service.spring_design_graph"
        ) as mock_graph:
            mock_graph.invoke.return_value = mock_graph_final_state

            from app.services.design_service import DesignService

            service = DesignService(db=mock_db_session)
            result = await service.clarify_design(
                session_id="session-123",
                answers="The load is 100N and OD max is 30mm.",
            )

        assert isinstance(result, DesignResponse)
        assert result.status == "approved"
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
                answers="Some answers",
            )

        assert exc_info.value.status_code == 404
        assert "not found" in exc_info.value.detail.casefold()


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
        from app.services.design_service import DesignService

        project = DesignProject(
            id=1,
            session_id="s-1",
            raw_user_input="test",
            status="approved",
            final_report={"status": "approved", "data": "ok"},
        )

        service = DesignService(db=mock_db_session)
        response = service._project_to_response(project)

        assert response.status == "approved"
        assert response.report == {"status": "approved", "data": "ok"}
        assert response.clarification_questions is None
        assert response.errors is None

    async def test_clarification_project(
        self, mock_db_session: AsyncMock
    ) -> None:
        """Verifica la conversion de un proyecto que requiere clarificacion."""
        from app.services.design_service import DesignService

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

        service = DesignService(db=mock_db_session)
        response = service._project_to_response(project)

        assert response.status == "needs_clarification"
        assert response.report is None
        assert response.clarification_questions == ["What force?"]
        assert response.errors is None

    async def test_error_project(self, mock_db_session: AsyncMock) -> None:
        """Verifica la conversion de un proyecto con error."""
        from app.services.design_service import DesignService

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

        service = DesignService(db=mock_db_session)
        response = service._project_to_response(project)

        assert response.status == "error"
        assert response.report is None
        assert response.errors == [{"step": "graph", "message": "fail"}]
