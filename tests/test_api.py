"""
tests/test_api.py
─────────────────────────────────────────────────────────────────────────────
Tests para los endpoints de la FastAPI del Spring Design Agent.

Se mockean todas las dependencias externas (DB, DesignService) para evitar
conexiones reales a base de datos o invocaciones reales a LangGraph/LLMs.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.schemas.design import DesignResponse


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_design_service() -> AsyncMock:
    """
    Retorna un DesignService mockeado que retorna respuestas controladas.

    Todos los metodos del servicio son reemplazados por AsyncMock para que
    los tests de API verifiquen unicamente el enrutamiento HTTP, no la
    logica interna del servicio.
    """
    service = AsyncMock()

    async def mock_start(*args: object, **kwargs: object) -> DesignResponse:
        return DesignResponse(
            session_id="test-session-123",
            status="approved",
            report={
                "status": "approved",
                "summary": "Design OK",
                "spring_type": "compression",
                "geometry": {"wire_diameter_mm": 3.5},
                "material": {"name": "ASTM A228 Music Wire"},
                "compliance": {
                    "approved": True,
                    "safety_factor_shear": 2.1,
                },
                "commercial": {"ranked_proposals": [], "chart_data": []},
                "three_js_scene": {},
                "generated_at": "2025-01-01T00:00:00+00:00",
            },
            clarification_questions=None,
            errors=None,
        )

    async def mock_clarify(*args: object, **kwargs: object) -> DesignResponse:
        return DesignResponse(
            session_id="test-session-456",
            status="approved",
            report={
                "status": "approved",
                "summary": "Design OK after clarification",
                "spring_type": "compression",
            },
            clarification_questions=None,
            errors=None,
        )

    async def mock_clarify_not_found(
        *args: object, **kwargs: object
    ) -> DesignResponse | None:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=404,
            detail="Session 'nonexistent' not found.",
        )

    async def mock_get_found(
        session_id: str,
    ) -> DesignResponse | None:
        return DesignResponse(
            session_id=session_id,
            status="approved",
            report={"status": "approved", "summary": "Retrieved design"},
            clarification_questions=None,
            errors=None,
        )

    async def mock_get_not_found(
        session_id: str,
    ) -> DesignResponse | None:
        return None

    service.start_design = mock_start
    service.clarify_design = mock_clarify
    service.get_design = mock_get_found

    return service


@pytest.fixture
def client(mock_design_service: AsyncMock) -> AsyncClient:
    """
    Retorna un AsyncClient de httpx conectado a la app FastAPI con las
    dependencias de servicio sobrescritas por el mock.
    """
    from app.api.v1.design import get_design_service

    app.dependency_overrides[get_design_service] = lambda: mock_design_service
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture
def client_without_session(
    mock_design_service: AsyncMock,
) -> AsyncClient:
    """
    Cliente con get_design configurado para retornar None (sesion no existe).
    """
    from app.api.v1.design import get_design_service

    async def mock_get_not_found(
        session_id: str,
    ) -> DesignResponse | None:
        return None

    mock_design_service.get_design = mock_get_not_found
    app.dependency_overrides[get_design_service] = lambda: mock_design_service
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestRootEndpoint:
    """Tests para el endpoint GET /.

    NOTA: Cuando el directorio ``frontend/out/`` existe, la app registra
    un catch-all ``/{full_path:path}`` que sirve el SPA y sombrea la ruta
    ``/`` definida en main.py.  Estos tests se adaptan a ambos escenarios.
    """

    async def test_root_returns_response(self, client: AsyncClient) -> None:
        """
        Verifica que GET / retorna una respuesta HTTP valida.
        Con frontend: sirve el SPA (HTML).  Sin frontend: retorna JSON.
        """
        response = await client.get("/")
        assert response.status_code == 200

    async def test_root_json_when_no_frontend(
        self, client: AsyncClient
    ) -> None:
        """
        Verifica que GET / retorna JSON con los metadatos de la API
        cuando el frontend NO esta presente.

        Si el frontend existe, la ruta es sombreada y este test se
        omite implicitamente (el assert no se cumple).
        """
        import os

        frontend_path = (
            os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "..",
                "frontend",
                "out",
            )
        )
        if os.path.isdir(frontend_path):
            pytest.skip("Frontend presente — ruta / sombreada por el catch-all SPA")

        response = await client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert data["service"] == "Spring Design Agent API"
        assert data["version"] == "0.1.0"
        assert data["status"] == "running"


class TestHealthEndpoint:
    """Tests para el endpoint GET /health.

    /health SIEMPRE retorna 200 independientemente de si el frontend
    existe, porque la ruta se registra explicitamente ANTES del catch-all
    ``/{full_path:path}`` y FastAPI resuelve rutas mas especificas primero.
    """

    async def test_health_returns_healthy(self, client: AsyncClient) -> None:
        """
        Verifica que GET /health retorna un status healthy siempre.
        El catch-all del frontend NO sombrea /health porque se registra
        despues en el orden de rutas de FastAPI.
        """
        response = await client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["version"] == "0.1.0"

    async def test_health_is_json_response(self, client: AsyncClient) -> None:
        """Verifica que /health retorna content-type JSON en cualquier escenario."""
        response = await client.get("/health")
        assert response.headers["content-type"] == "application/json"


class TestStartDesign:
    """Tests para POST /api/v1/design/."""

    async def test_start_design_valid_input(self, client: AsyncClient) -> None:
        """
        Verifica que POST /api/v1/design/ con input valido retorna 200
        y un DesignResponse aprobado.
        """
        payload = {
            "user_input": "Design a compression spring for 120N",
            "max_iterations": 5,
        }
        response = await client.post("/api/v1/design/", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["session_id"] == "test-session-123"
        assert data["status"] == "approved"
        assert data["report"] is not None
        assert data["clarification_questions"] is None

    async def test_start_design_default_iterations(
        self, client: AsyncClient
    ) -> None:
        """
        Verifica que POST /api/v1/design/ funciona con solo user_input
        (usa valor por defecto para max_iterations).
        """
        payload = {
            "user_input": "I need a torsion spring, 5Nm at 45 degrees",
        }
        response = await client.post("/api/v1/design/", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["session_id"] == "test-session-123"

    async def test_start_design_short_input_returns_422(
        self, client: AsyncClient
    ) -> None:
        """
        Verifica que POST /api/v1/design/ con input menor a 5 caracteres
        retorna 422 Unprocessable Entity.
        """
        payload = {"user_input": "abc"}
        response = await client.post("/api/v1/design/", json=payload)

        assert response.status_code == 422
        data = response.json()
        assert "detail" in data

    async def test_start_design_empty_input_returns_422(
        self, client: AsyncClient
    ) -> None:
        """Verifica que input vacio retorna 422."""
        payload = {"user_input": ""}
        response = await client.post("/api/v1/design/", json=payload)

        assert response.status_code == 422

    async def test_start_design_missing_input_returns_422(
        self, client: AsyncClient
    ) -> None:
        """Verifica que omitir user_input retorna 422."""
        payload: dict = {}
        response = await client.post("/api/v1/design/", json=payload)

        assert response.status_code == 422

    async def test_start_design_invalid_iterations_type(
        self, client: AsyncClient
    ) -> None:
        """Verifica que max_iterations no entero retorna 422."""
        payload = {
            "user_input": "Design a compression spring",
            "max_iterations": "five",
        }
        response = await client.post("/api/v1/design/", json=payload)

        assert response.status_code == 422

    async def test_start_design_out_of_range_iterations(
        self, client: AsyncClient
    ) -> None:
        """Verifica que max_iterations fuera de rango [1,10] retorna 422."""
        payload = {
            "user_input": "Design a spring",
            "max_iterations": 15,
        }
        response = await client.post("/api/v1/design/", json=payload)

        assert response.status_code == 422

    async def test_start_design_with_session_id(
        self, client: AsyncClient
    ) -> None:
        """
        Verifica que se puede pasar un session_id opcional en el request.
        """
        payload = {
            "user_input": "Design a compression spring",
            "session_id": "custom-session-001",
        }
        response = await client.post("/api/v1/design/", json=payload)

        assert response.status_code == 200
        # El mock siempre retorna "test-session-123", no el enviado
        data = response.json()
        assert data["session_id"] == "test-session-123"


class TestClarifyDesign:
    """Tests para POST /api/v1/design/clarify."""

    async def test_clarify_design_valid(
        self, mock_design_service: AsyncMock, client: AsyncClient
    ) -> None:
        """
        Verifica que POST /api/v1/design/clarify con datos validos
        retorna 200.
        """
        payload = {
            "session_id": "test-session-456",
            "answers": ["The required force is 100N and OD max is 30mm."],
        }
        response = await client.post("/api/v1/design/clarify", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["session_id"] == "test-session-456"
        assert data["status"] == "approved"

    async def test_clarify_design_unknown_session_returns_404(
        self, mock_design_service: AsyncMock, client: AsyncClient
    ) -> None:
        """
        Verifica que clarify_design retorna 404 cuando la sesion no existe.

        Se configura el mock para que lance HTTPException 404.
        """
        from fastapi import HTTPException

        async def mock_not_found(
            *args: object, **kwargs: object
        ) -> DesignResponse:
            raise HTTPException(
                status_code=404,
                detail="Session 'nonexistent' not found.",
            )

        mock_design_service.clarify_design = mock_not_found

        payload = {
            "session_id": "nonexistent",
            "answers": ["Some answers."],
        }
        response = await client.post("/api/v1/design/clarify", json=payload)

        assert response.status_code == 404
        data = response.json()
        assert "not found" in data["detail"].casefold()

    async def test_clarify_design_missing_session_id_returns_422(
        self, client: AsyncClient
    ) -> None:
        """Verifica que omitir session_id retorna 422."""
        payload = {"answers": "Some answers."}
        response = await client.post("/api/v1/design/clarify", json=payload)

        assert response.status_code == 422

    async def test_clarify_design_missing_answers_returns_422(
        self, client: AsyncClient
    ) -> None:
        """Verifica que omitir answers retorna 422."""
        payload = {"session_id": "test-session"}
        response = await client.post("/api/v1/design/clarify", json=payload)

        assert response.status_code == 422

    async def test_clarify_design_empty_answers_returns_422(
        self, client: AsyncClient
    ) -> None:
        """
        Verifica que answers vacio retorna 422 (min_length=1).
        """
        payload = {"session_id": "test-session", "answers": []}
        response = await client.post("/api/v1/design/clarify", json=payload)

        assert response.status_code == 422


class TestGetDesign:
    """Tests para GET /api/v1/design/{session_id}."""

    async def test_get_design_found(self, client: AsyncClient) -> None:
        """
        Verifica que GET /api/v1/design/{session_id} retorna el diseno
        cuando la sesion existe.
        """
        response = await client.get("/api/v1/design/test-session-789")

        assert response.status_code == 200
        data = response.json()
        assert data["session_id"] == "test-session-789"
        assert data["status"] == "approved"
        assert data["report"] is not None

    async def test_get_design_not_found(
        self, client_without_session: AsyncClient
    ) -> None:
        """
        Verifica que GET /api/v1/design/{session_id} retorna 404 cuando
        la sesion no existe.
        """
        response = await client_without_session.get(
            "/api/v1/design/nonexistent"
        )

        assert response.status_code == 404
        data = response.json()
        assert "not found" in data["detail"].casefold()


class TestLLMHealth:
    """Tests para GET /api/v1/design/health/llm."""

    async def test_llm_health_returns_status(self, client: AsyncClient) -> None:
        """
        Verifica que GET /api/v1/design/health/llm retorna informacion
        del proveedor LLM activo.
        """
        response = await client.get("/api/v1/design/health/llm")

        assert response.status_code == 200
        data = response.json()
        # Verificar que las claves esperadas estan presentes
        assert "active_provider" in data
        assert "failed_providers" in data
        assert "priority_order" in data

    async def test_llm_health_is_json(self, client: AsyncClient) -> None:
        """Verifica que el endpoint de health/llm retorna JSON."""
        response = await client.get("/api/v1/design/health/llm")
        assert response.headers["content-type"] == "application/json"
