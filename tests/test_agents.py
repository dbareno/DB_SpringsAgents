"""
tests/test_agents.py
─────────────────────────────────────────────────────────────────────────────
Tests para los nodos de agente del LangGraph del Spring Design Agent.

Cada nodo de agente se prueba de forma aislada mockeando las llamadas al
LLM (via get_factory/get_llm) y a las herramientas externas. No se realizan
llamadas reales a ningun proveedor de LLM ni base de datos.
"""

from __future__ import annotations

import json
from typing import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage

from app.schemas.state import (
    AgentState,
    ComplianceReport,
    MaterialProperties,
    SpringGeometry,
    UserRequirements,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _make_llm_mock(content: str) -> MagicMock:
    """
    Construye un mock de BaseChatModel que retorna un AIMessage con el
    contenido JSON especificado.

    NOTA: Usamos MagicMock (no AsyncMock) porque los nodos de agente
    invocan llm.invoke() de forma SINCRONICA (no await).
    """
    llm = MagicMock()
    llm.invoke.return_value = AIMessage(content=content)
    return llm


def _make_factory_mock(llm_mock: AsyncMock) -> MagicMock:
    """
    Construye un mock de LLMFactory cuya configuracion tiene 2 proveedores
    y cuyo get_llm retorna el mock provisto.
    """
    factory = MagicMock()
    factory.get_llm.return_value = llm_mock
    # _settings con llm_priority_order de 2 proveedores
    settings_mock = MagicMock()
    settings_mock.llm_priority_order = ["gemini", "openai"]
    factory._settings = settings_mock
    factory._priority_order = ["gemini", "openai"]
    return factory


def _make_agent_state(**overrides: object) -> AgentState:
    """Construye un AgentState base con valores por defecto."""
    state = AgentState({
        "messages": [],
        "current_step": "start",
        "iteration_count": 0,
        "max_iterations": 5,
        "requirements": None,
        "geometry": None,
        "material": None,
        "compliance": None,
        "commercial_proposals": [],
        "llm_status": None,
        "errors": [],
        "final_report": None,
        "_raw_input": "",
        "redesign_directives": [],
    })
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


# ─────────────────────────────────────────────────────────────────────────────
# Agent 1 — Requirements Analyst
# ─────────────────────────────────────────────────────────────────────────────


class TestAgent1Requirements:
    """Tests para agent1_requirements.requirements_analyst_node."""

    COMPLETE_JSON = json.dumps({
        "spring_type": "compression",
        "load_force_n": 120.0,
        "deflection_mm": 15.0,
        "spring_rate_n_mm": None,
        "max_outer_diameter_mm": 25.0,
        "max_free_length_mm": 60.0,
        "solid_length_mm": None,
        "operating_temperature_c": 20.0,
        "corrosion_resistant": False,
        "cyclic_load": False,
        "cycles_expected": None,
        "clarification_questions": [],
        "is_complete": True,
    })

    INCOMPLETE_JSON = json.dumps({
        "spring_type": "unknown",
        "load_force_n": None,
        "deflection_mm": None,
        "spring_rate_n_mm": None,
        "max_outer_diameter_mm": None,
        "max_free_length_mm": None,
        "solid_length_mm": None,
        "operating_temperature_c": None,
        "corrosion_resistant": False,
        "cyclic_load": False,
        "cycles_expected": None,
        "clarification_questions": [
            "What is the required load force in Newtons?",
            "What is the required deflection in mm?",
        ],
        "is_complete": False,
    })

    @pytest.fixture(autouse=True)
    def _patch_factory(self, request: pytest.FixtureRequest) -> None:
        """Parchea get_factory en agent1_requirements para cada test."""
        patcher = patch(
            "app.agents.agent1_requirements.get_factory"
        )
        self._mock_get_factory = patcher.start()
        request.addfinalizer(patcher.stop)

    def _setup_llm(self, content: str) -> AsyncMock:
        """Configura el mock del factory para retornar un LLM controlado."""
        llm = _make_llm_mock(content)
        factory = _make_factory_mock(llm)
        self._mock_get_factory.return_value = factory
        return llm

    def test_complete_input_returns_user_requirements(self) -> None:
        """
        Verifica que con input completo el agente retorna un UserRequirements
        con is_complete=True y sin preguntas de clarificacion.
        """
        self._setup_llm(self.COMPLETE_JSON)

        from app.agents.agent1_requirements import requirements_analyst_node

        state = _make_agent_state(
            _raw_input="Design a compression spring for 120N, 15mm deflection",
        )
        result = requirements_analyst_node(state)

        assert "requirements" in result
        req: UserRequirements = result["requirements"]
        assert isinstance(req, UserRequirements)
        assert req.is_complete is True
        assert req.load_force_n == 120.0
        assert req.deflection_mm == 15.0
        assert req.spring_type == "compression"
        assert req.clarification_questions == []
        assert result["current_step"] == "requirements_analyst"

    def test_incomplete_input_missing_force(self) -> None:
        """
        Verifica que cuando faltan campos criticos el agente retorna
        is_complete=False con preguntas de clarificacion.
        """
        self._setup_llm(self.INCOMPLETE_JSON)

        from app.agents.agent1_requirements import requirements_analyst_node

        state = _make_agent_state(
            _raw_input="I need a spring for a pen",
        )
        result = requirements_analyst_node(state)

        req: UserRequirements = result["requirements"]
        assert req.is_complete is False
        # _determine_completeness generates questions for ALL missing fields
        # (type, force, deflection, rate, OD, free length, temp)
        assert len(req.clarification_questions) == 7
        assert "tipo de resorte" in req.clarification_questions[0].lower()
        assert "fuerza" in req.clarification_questions[1].lower()

    def test_invalid_json_returns_error(self) -> None:
        """
        Verifica que cuando el LLM retorna JSON invalido el agente retorna
        un error en lugar de crash.
        """
        self._setup_llm("not valid json at all")

        from app.agents.agent1_requirements import requirements_analyst_node

        state = _make_agent_state(
            _raw_input="Design a spring",
        )
        result = requirements_analyst_node(state)

        assert "requirements" not in result
        assert "errors" in result
        assert len(result["errors"]) > 0
        error = result["errors"][0]
        assert error["step"] == "requirements_analyst"
        assert error["error_type"] == "InvalidJSON"

    def test_fallback_to_last_human_message(self) -> None:
        """
        Verifica que si _raw_input esta vacio, el agente usa el ultimo
        mensaje humano como fallback para raw_input.
        """
        self._setup_llm(self.COMPLETE_JSON)

        from app.agents.agent1_requirements import requirements_analyst_node
        from langchain_core.messages import HumanMessage

        state = _make_agent_state(
            _raw_input="",
            messages=[HumanMessage(content="Fallback spring input")],
        )
        result = requirements_analyst_node(state)

        assert "requirements" in result
        req: UserRequirements = result["requirements"]
        # El raw_input se toma del último HumanMessage cuando _raw_input está vacío
        assert req.raw_input == "Fallback spring input"

    def test_empty_raw_input_no_messages(self) -> None:
        """
        Verifica que si no hay _raw_input ni mensajes humanos, el agente
        aun asi ejecuta el LLM pero con string vacio.
        """
        self._setup_llm(self.COMPLETE_JSON)

        from app.agents.agent1_requirements import requirements_analyst_node

        state = _make_agent_state()
        result = requirements_analyst_node(state)

        assert "requirements" in result
        req: UserRequirements = result["requirements"]
        assert req.raw_input == ""


# ─────────────────────────────────────────────────────────────────────────────
# Agent 2 — Design Engineer
# ─────────────────────────────────────────────────────────────────────────────


class TestAgent2Design:
    """Tests para agent2_design.design_engineer_node."""

    @pytest.fixture(autouse=True)
    def _patch_factory(self, request: pytest.FixtureRequest) -> None:
        """Parchea get_factory en agent2_design para cada test."""
        patcher = patch("app.agents.agent2_design.get_factory")
        self._mock_get_factory = patcher.start()
        request.addfinalizer(patcher.stop)

    @pytest.fixture(autouse=True)
    def _patch_tool(self, request: pytest.FixtureRequest) -> None:
        """
        Parchea calculate_spring_geometry_tool.invoke para retornar una
        geometria controlada.
        """
        patcher = patch(
            "app.agents.agent2_design.calculate_spring_geometry_tool"
        )
        self._mock_tool = patcher.start()
        request.addfinalizer(patcher.stop)

    def _mock_geometry_tool(self, status: str = "ok") -> None:
        """Configura el mock de la herramienta de geometria."""
        self._mock_tool.invoke.return_value = json.dumps({
            "status": status,
            "geometry": {
                "wire_diameter_mm": 3.5,
                "mean_coil_diameter_mm": 28.0,
                "outer_diameter_mm": 31.5,
                "inner_diameter_mm": 24.5,
                "active_coils": 8.0,
                "total_coils": 10.0,
                "free_length_mm": 60.0,
                "pitch_mm": 7.5,
                "spring_index": 8.0,
                "spring_rate_n_mm": 5.0,
                "torsion_moment_n_mm": None,
                "angular_deflection_deg": None,
            },
        })

    def _make_requirements(self, **overrides: object) -> UserRequirements:
        """Construye un UserRequirements con valores por defecto."""
        params = {
            "raw_input": "Design a compression spring",
            "spring_type": "compression",
            "load_force_n": 100.0,
            "deflection_mm": 20.0,
            "spring_rate_n_mm": None,
            "max_outer_diameter_mm": None,
            "max_free_length_mm": None,
            "solid_length_mm": None,
            "operating_temperature_c": None,
            "corrosion_resistant": False,
            "cyclic_load": False,
            "cycles_expected": None,
            "clarification_questions": [],
            "is_complete": True,
        }
        params.update(overrides)
        return UserRequirements(**params)

    def test_with_force_and_deflection_calls_tool_directly(self) -> None:
        """
        Verifica que cuando hay load_force_n y deflection_mm, el agente
        llama la herramienta directamente sin pasar por el LLM.
        """
        self._mock_geometry_tool()

        from app.agents.agent2_design import design_engineer_node

        requirements = self._make_requirements(
            load_force_n=100.0,
            deflection_mm=20.0,
        )
        state = _make_agent_state(requirements=requirements)
        result = design_engineer_node(state)

        # Verificar que NO se llamo al LLM
        self._mock_get_factory.assert_not_called()

        # Verificar que la herramienta fue invocada
        self._mock_tool.invoke.assert_called_once()

        assert "geometry" in result
        geom: SpringGeometry = result["geometry"]
        assert isinstance(geom, SpringGeometry)
        assert geom.wire_diameter_mm == 3.5
        assert geom.spring_rate_n_mm == 5.0
        assert result["current_step"] == "design_engineer"

    def test_with_spring_rate_computes_deflection(self) -> None:
        """
        Verifica que cuando hay spring_rate_n_mm, el agente calcula
        deflection = force / rate antes de llamar la herramienta.
        """
        self._mock_geometry_tool()

        from app.agents.agent2_design import design_engineer_node

        requirements = self._make_requirements(
            load_force_n=100.0,
            deflection_mm=None,
            spring_rate_n_mm=5.0,
        )
        state = _make_agent_state(requirements=requirements)
        result = design_engineer_node(state)

        # La herramienta debe recibir deflection_mm = 100/5 = 20.0
        self._mock_tool.invoke.assert_called_once()
        tool_kwargs = self._mock_tool.invoke.call_args[0][0]
        assert tool_kwargs["deflection_mm"] == 20.0

        assert "geometry" in result

    def test_missing_requirements_returns_error(self) -> None:
        """
        Verifica que cuando requirements es None, el agente retorna
        un error.
        """
        from app.agents.agent2_design import design_engineer_node

        state = _make_agent_state(requirements=None)
        result = design_engineer_node(state)

        assert "geometry" not in result
        assert "errors" in result
        assert result["errors"][0]["error_type"] == "MissingRequirements"

    def test_uses_material_properties_when_available(self) -> None:
        """
        Verifica que cuando hay material en el estado, el agente usa
        sus propiedades (G, Sy) en lugar de los defaults.
        """
        self._mock_geometry_tool()

        from app.agents.agent2_design import design_engineer_node

        material = MaterialProperties(
            material_id=3,
            name="ASTM A313 Stainless Steel",
            shear_modulus_gpa=69.0,
            elastic_modulus_gpa=193.0,
            density_kg_m3=7920.0,
            yield_strength_mpa=1100.0,
            ultimate_strength_mpa=1380.0,
            max_temp_c=260.0,
            corrosion_resistant=True,
            cost_usd_per_kg=9.50,
        )
        requirements = self._make_requirements()
        state = _make_agent_state(
            requirements=requirements,
            material=material,
        )
        result = design_engineer_node(state)

        self._mock_tool.invoke.assert_called_once()
        tool_kwargs = self._mock_tool.invoke.call_args[0][0]
        # Debe usar el shear_modulus_gpa del material (69.0), no el default
        assert tool_kwargs["shear_modulus_gpa"] == 69.0
        assert tool_kwargs["yield_strength_mpa"] == 1100.0

        assert "geometry" in result


# ─────────────────────────────────────────────────────────────────────────────
# Agent 3 — Materials Engineer
# ─────────────────────────────────────────────────────────────────────────────


class TestAgent3Materials:
    """Tests para agent3_materials.materials_engineer_node (v2)."""

    # Candidates now include score/bonus fields produced by _score_candidates
    SINGLE_CANDIDATE = [
        {
            "material_id": 1,
            "name": "ASTM A228 Music Wire",
            "shear_modulus_gpa": 81.5,
            "elastic_modulus_gpa": 207.0,
            "density_kg_m3": 7850.0,
            "yield_strength_mpa": 1580.0,
            "ultimate_strength_mpa": 1900.0,
            "max_temp_c": 120.0,
            "corrosion_resistant": False,
            "cost_usd_per_kg": 3.80,
            "score": 415.8,
            "temp_bonus": 4.8,
            "fatigue_bonus": 1.0,
            "preference_bonus": 1.0,
        },
    ]

    MULTIPLE_CANDIDATES = [
        {
            "material_id": 1,
            "name": "ASTM A228 Music Wire",
            "shear_modulus_gpa": 81.5,
            "elastic_modulus_gpa": 207.0,
            "density_kg_m3": 7850.0,
            "yield_strength_mpa": 1580.0,
            "ultimate_strength_mpa": 1900.0,
            "max_temp_c": 120.0,
            "corrosion_resistant": False,
            "cost_usd_per_kg": 3.80,
            "score": 415.8,
            "temp_bonus": 4.8,
            "fatigue_bonus": 1.0,
            "preference_bonus": 1.0,
        },
        {
            "material_id": 3,
            "name": "ASTM A313 Type 302 Stainless Steel",
            "shear_modulus_gpa": 69.0,
            "elastic_modulus_gpa": 193.0,
            "density_kg_m3": 7920.0,
            "yield_strength_mpa": 1100.0,
            "ultimate_strength_mpa": 1380.0,
            "max_temp_c": 260.0,
            "corrosion_resistant": True,
            "cost_usd_per_kg": 9.50,
            "score": 115.8,
            "temp_bonus": 10.4,
            "fatigue_bonus": 1.0,
            "preference_bonus": 1.0,
        },
    ]

    @pytest.fixture(autouse=True)
    def _patch_factory(self, request: pytest.FixtureRequest) -> None:
        """Parchea get_factory en agent3_materials."""
        patcher = patch("app.agents.agent3_materials.get_factory")
        self._mock_get_factory = patcher.start()
        request.addfinalizer(patcher.stop)

    @pytest.fixture(autouse=True)
    def _patch_tool(self, request: pytest.FixtureRequest) -> None:
        """Parchea query_material_properties_tool.invoke."""
        patcher = patch(
            "app.agents.agent3_materials.query_material_properties_tool"
        )
        self._mock_tool = patcher.start()
        request.addfinalizer(patcher.stop)

    def _setup_llm(self, material_id: int = 1) -> None:
        """Configure LLM mock to return a dummy selection."""
        llm_response = json.dumps({
            "selected_material_id": material_id,
            "justification": "Best strength-to-cost ratio for this application.",
            "runner_up_name": None,
            "runner_up_reason": None,
            "candidate_summary": "2 candidates evaluated.",
        })
        llm = _make_llm_mock(llm_response)
        factory = _make_factory_mock(llm)
        self._mock_get_factory.return_value = factory

    def _make_requirements(self, raw_input: str = "Design a compression spring") -> UserRequirements:
        return UserRequirements(
            raw_input=raw_input,
            spring_type="compression",
            load_force_n=100.0,
            deflection_mm=20.0,
            operating_temperature_c=25.0,
            corrosion_resistant=False,
            cyclic_load=False,
        )

    def _state_with_requirements(
        self,
        raw_input: str = "Design a compression spring",
        **overrides: object,
    ) -> AgentState:
        req = self._make_requirements(raw_input=raw_input)
        return _make_agent_state(requirements=req, _raw_input=raw_input, **overrides)

    # ── Tests ────────────────────────────────────────────────────────────

    def test_single_candidate_selects_with_llm(self) -> None:
        """
        Verifica que incluso con un solo candidato, el agente usa el LLM
        para generar justificacion (no hay fast-path).
        """
        self._mock_tool.invoke.return_value = json.dumps({
            "status": "ok",
            "candidates": self.SINGLE_CANDIDATE,
        })
        self._setup_llm(material_id=1)

        from app.agents.agent3_materials import materials_engineer_node

        state = self._state_with_requirements()
        result = materials_engineer_node(state)

        # Debe llamar al LLM incluso con un solo candidato
        self._mock_get_factory.assert_called()

        assert "material" in result
        mat: MaterialProperties = result["material"]
        assert isinstance(mat, MaterialProperties)
        assert mat.name == "ASTM A228 Music Wire"

    def test_multiple_candidates_selects_best(self) -> None:
        """
        Verifica que con multiples candidatos el agente invoca el LLM
        y selecciona el material correcto.
        """
        self._mock_tool.invoke.return_value = json.dumps({
            "status": "ok",
            "candidates": self.MULTIPLE_CANDIDATES,
        })
        self._setup_llm(material_id=3)

        from app.agents.agent3_materials import materials_engineer_node

        state = self._state_with_requirements()
        result = materials_engineer_node(state)

        self._mock_get_factory.assert_called()
        assert "material" in result
        mat: MaterialProperties = result["material"]
        assert mat.material_id == 3
        assert mat.name == "ASTM A313 Type 302 Stainless Steel"

    def test_preferred_material_passed_to_tool(self) -> None:
        """
        Verifica que si el raw_input menciona un material ('stainless'),
        el agente lo pasa como preferred_material_name al tool.
        """
        self._mock_tool.invoke.return_value = json.dumps({
            "status": "ok",
            "candidates": self.MULTIPLE_CANDIDATES,
        })
        self._setup_llm(material_id=3)

        from app.agents.agent3_materials import materials_engineer_node

        state = self._state_with_requirements(
            raw_input="I need a stainless steel spring for a valve",
        )
        result = materials_engineer_node(state)

        # Verificar que preferred_material_name se paso al tool
        self._mock_tool.invoke.assert_called_once()
        tool_kwargs = self._mock_tool.invoke.call_args[0][0]
        assert "preferred_material_name" in tool_kwargs
        assert tool_kwargs["preferred_material_name"] == "stainless steel"

        assert "material" in result

    def test_no_matching_material_returns_error(self) -> None:
        """
        Verifica que cuando la herramienta retorna no_match, el agente
        retorna un error (con mensaje explicativo opcional).
        """
        self._mock_tool.invoke.return_value = json.dumps({
            "status": "no_match",
            "message": "No material satisfies all constraints.",
        })

        from app.agents.agent3_materials import materials_engineer_node

        state = self._state_with_requirements()
        result = materials_engineer_node(state)

        assert "material" not in result
        assert "errors" in result
        assert result["errors"][0]["error_type"] == "NoMaterialMatch"

    def test_tool_error_returns_error(self) -> None:
        """
        Verifica que cuando la herramienta retorna status error,
        el agente propaga el error.
        """
        self._mock_tool.invoke.return_value = json.dumps({
            "status": "error",
            "message": "Something went wrong",
        })

        from app.agents.agent3_materials import materials_engineer_node

        state = self._state_with_requirements()
        result = materials_engineer_node(state)

        assert "material" not in result
        assert result["errors"][0]["error_type"] == "ToolError"

    def test_missing_requirements_returns_error(self) -> None:
        """
        Verifica que cuando requirements es None, el agente retorna
        error.
        """
        from app.agents.agent3_materials import materials_engineer_node

        state = _make_agent_state(requirements=None)
        result = materials_engineer_node(state)

        assert "material" not in result
        assert result["errors"][0]["error_type"] == "MissingRequirements"

    def test_tool_exception_returns_error(self) -> None:
        """
        Verifica que cuando la herramienta lanza una excepcion,
        el agente la captura y retorna error.
        """
        self._mock_tool.invoke.side_effect = RuntimeError("Connection failed")

        from app.agents.agent3_materials import materials_engineer_node

        state = self._state_with_requirements()
        result = materials_engineer_node(state)

        assert "material" not in result
        assert result["errors"][0]["error_type"] == "RuntimeError"

    # ── Material candidates short-list ───────────────────────────────────

    def test_material_candidates_populated(self) -> None:
        """
        Verifica que el agente expone una short-list de candidatos
        (material_candidates) ademas del material seleccionado.
        """
        self._mock_tool.invoke.return_value = json.dumps({
            "status": "ok",
            "candidates": self.MULTIPLE_CANDIDATES,
        })
        self._setup_llm(material_id=1)

        from app.agents.agent3_materials import materials_engineer_node

        state = self._state_with_requirements()
        result = materials_engineer_node(state)

        assert "material_candidates" in result
        candidates = result["material_candidates"]
        assert len(candidates) == 2
        assert all(isinstance(c, MaterialProperties) for c in candidates)
        # El material seleccionado debe estar incluido en la short-list
        assert any(c.material_id == 1 for c in candidates)
        # Deduplicado por material_id
        ids = [c.material_id for c in candidates]
        assert len(ids) == len(set(ids))

    def test_material_candidates_capped_at_three(self) -> None:
        """
        Verifica que la short-list se limita a los 3 mejores candidatos
        (en el orden de ranking del tool).
        """
        extra = [
            {**self.MULTIPLE_CANDIDATES[0], "material_id": 5,
             "name": "ASTM A401 Chrome-Silicon (SAE 9254)", "score": 300.0},
            {**self.MULTIPLE_CANDIDATES[0], "material_id": 6,
             "name": "DIN 17223-C Chrome-Vanadium (VD-SiCr)", "score": 200.0},
        ]
        four_candidates = [
            self.MULTIPLE_CANDIDATES[0],
            extra[0],
            extra[1],
            self.MULTIPLE_CANDIDATES[1],
        ]
        self._mock_tool.invoke.return_value = json.dumps({
            "status": "ok",
            "candidates": four_candidates,
        })
        self._setup_llm(material_id=1)

        from app.agents.agent3_materials import materials_engineer_node

        state = self._state_with_requirements()
        result = materials_engineer_node(state)

        candidates = result["material_candidates"]
        assert len(candidates) == 3
        # Orden de ranking del tool preservado
        assert [c.material_id for c in candidates] == [1, 5, 6]

    def test_material_candidates_include_selected_outside_top3(self) -> None:
        """
        Verifica que si el LLM selecciona un material fuera del top-3,
        la short-list igual lo incluye.
        """
        extra = [
            {**self.MULTIPLE_CANDIDATES[0], "material_id": 5,
             "name": "ASTM A401 Chrome-Silicon (SAE 9254)", "score": 300.0},
            {**self.MULTIPLE_CANDIDATES[0], "material_id": 6,
             "name": "DIN 17223-C Chrome-Vanadium (VD-SiCr)", "score": 200.0},
        ]
        four_candidates = [
            self.MULTIPLE_CANDIDATES[0],
            extra[0],
            extra[1],
            self.MULTIPLE_CANDIDATES[1],   # id 3 — ranked last
        ]
        self._mock_tool.invoke.return_value = json.dumps({
            "status": "ok",
            "candidates": four_candidates,
        })
        self._setup_llm(material_id=3)

        from app.agents.agent3_materials import materials_engineer_node

        state = self._state_with_requirements()
        result = materials_engineer_node(state)

        assert result["material"].material_id == 3
        candidates = result["material_candidates"]
        assert len(candidates) == 3
        assert any(c.material_id == 3 for c in candidates)

    # ── Unit tests for helpers ───────────────────────────────────────────

    def test_extract_user_material_preference_stainless(self) -> None:
        """Reconoce 'stainless' en el input."""
        from app.agents.agent3_materials import _extract_user_material_preference
        name, goal = _extract_user_material_preference(
            "I need a stainless steel spring"
        )
        assert name == "stainless steel"

    def test_extract_user_material_preference_cheap(self) -> None:
        """Reconoce metas como 'cheap'."""
        from app.agents.agent3_materials import _extract_user_material_preference
        name, goal = _extract_user_material_preference(
            "I need a cheap spring"
        )
        assert name is None  # cheap no es un material
        assert "cost" in goal.lower() or "low" in goal.lower()

    def test_extract_user_material_preference_bronze(self) -> None:
        """Reconoce 'bronze' como phosphor bronze."""
        from app.agents.agent3_materials import _extract_user_material_preference
        name, goal = _extract_user_material_preference(
            "bronze spring for marine use"
        )
        assert name == "phosphor bronze"


# ─────────────────────────────────────────────────────────────────────────────
# Agent 4 — Normative Inspector
# ─────────────────────────────────────────────────────────────────────────────


class TestAgent4Compliance:
    """Tests para agent4_compliance.normative_inspector_node."""

    @pytest.fixture(autouse=True)
    def _patch_tool(self, request: pytest.FixtureRequest) -> None:
        """Parchea compliance_verification_tool.invoke."""
        patcher = patch(
            "app.agents.agent4_compliance.compliance_verification_tool"
        )
        self._mock_tool = patcher.start()
        request.addfinalizer(patcher.stop)

    def _make_geometry(self) -> SpringGeometry:
        return SpringGeometry(
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

    def _make_material(self) -> MaterialProperties:
        return MaterialProperties(
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

    def _make_requirements(self) -> UserRequirements:
        return UserRequirements(
            raw_input="Design a spring",
            spring_type="compression",
            load_force_n=100.0,
            deflection_mm=20.0,
            cyclic_load=False,
        )

    def test_approved_design(self) -> None:
        """
        Verifica que un diseno que pasa todas las verificaciones
        retorna compliance con approved=True.
        """
        self._mock_tool.invoke.return_value = json.dumps({
            "status": "ok",
            "report": {
                "approved": True,
                "safety_factor_shear": 2.1,
                "safety_factor_buckling": 1.8,
                "safety_factor_fatigue": None,
                "spring_index": 8.0,
                "wahl_factor": 1.184,
                "corrected_shear_stress_mpa": 320.5,
                "slenderness_ratio": 2.14,
                "applicable_standard": "DIN 2095 / ASTM A125",
                "failure_modes": [],
                "redesign_directives": [],
            },
        })

        from app.agents.agent4_compliance import normative_inspector_node

        geometry = self._make_geometry()
        material = self._make_material()
        requirements = self._make_requirements()
        state = _make_agent_state(
            geometry=geometry,
            material=material,
            requirements=requirements,
        )
        result = normative_inspector_node(state)

        assert "compliance" in result
        comp: ComplianceReport = result["compliance"]
        assert isinstance(comp, ComplianceReport)
        assert comp.approved is True
        assert comp.safety_factor_shear == 2.1
        assert comp.safety_factor_buckling == 1.8
        assert comp.failure_modes == []
        assert result["current_step"] == "normative_approved"

    def test_rejected_high_slenderness(self) -> None:
        """
        Verifica que un diseno con slenderness alto (buckling risk)
        retorna compliance con approved=False y modos de falla.
        """
        self._mock_tool.invoke.return_value = json.dumps({
            "status": "ok",
            "report": {
                "approved": False,
                "safety_factor_shear": 1.5,
                "safety_factor_buckling": 0.5,
                "safety_factor_fatigue": None,
                "spring_index": 8.0,
                "wahl_factor": 1.184,
                "corrected_shear_stress_mpa": 320.5,
                "slenderness_ratio": 10.0,
                "applicable_standard": "DIN 2095 / ASTM A125",
                "failure_modes": [
                    "Buckling risk: slenderness = 10.00 > 5.26"
                ],
                "redesign_directives": [
                    "Reduce free length or increase coil diameter."
                ],
            },
        })

        from app.agents.agent4_compliance import normative_inspector_node

        geometry = self._make_geometry()
        material = self._make_material()
        requirements = self._make_requirements()
        state = _make_agent_state(
            geometry=geometry,
            material=material,
            requirements=requirements,
        )
        result = normative_inspector_node(state)

        comp: ComplianceReport = result["compliance"]
        assert comp.approved is False
        assert len(comp.failure_modes) > 0
        assert any("buckling" in fm.lower() for fm in comp.failure_modes)
        assert result["current_step"] == "redesign_needed"

    def test_missing_dependencies_returns_error(self) -> None:
        """
        Verifica que cuando geometry o material son None, el agente
        retorna error.
        """
        from app.agents.agent4_compliance import normative_inspector_node

        state = _make_agent_state(geometry=None, material=None)
        result = normative_inspector_node(state)

        assert "compliance" not in result
        assert result["errors"][0]["error_type"] == "MissingDependencies"

    def test_tool_error_returns_error(self) -> None:
        """
        Verifica que cuando la herramienta retorna error, el agente
        propaga el error.
        """
        self._mock_tool.invoke.side_effect = RuntimeError("Tool crashed")

        from app.agents.agent4_compliance import normative_inspector_node

        geometry = self._make_geometry()
        material = self._make_material()
        state = _make_agent_state(
            geometry=geometry,
            material=material,
            requirements=self._make_requirements(),
        )
        result = normative_inspector_node(state)

        assert "compliance" not in result
        assert result["errors"][0]["error_type"] == "RuntimeError"

    def test_retrieval_hit_populates_retrieved_standards(self) -> None:
        """
        Cuando retrieve_standards() encuentra clausulas relevantes, el
        ComplianceReport debe incluirlas en retrieved_standards /
        standards_referenced y el mensaje narrativo debe citarlas.
        """
        self._mock_tool.invoke.return_value = json.dumps({
            "status": "ok",
            "report": {
                "approved": True,
                "safety_factor_shear": 2.1,
                "safety_factor_buckling": 1.8,
                "safety_factor_fatigue": None,
                "spring_index": 8.0,
                "wahl_factor": 1.184,
                "corrected_shear_stress_mpa": 320.5,
                "slenderness_ratio": 2.14,
                "applicable_standard": "DIN 2095 / ASTM A125",
                "failure_modes": [],
                "redesign_directives": [],
            },
        })

        from app.agents.agent4_compliance import normative_inspector_node
        from app.standards.retrieval import StandardsChunk

        dummy_chunks = [
            StandardsChunk(
                standard_name="DIN 2095",
                chunk_index=0,
                chunk_text="The spring index C shall be between 4 and 20.",
                distance=0.05,
            ),
            StandardsChunk(
                standard_name="ASTM A125",
                chunk_index=1,
                chunk_text="Stress-relieve at 200C for 20 minutes after coiling.",
                distance=0.12,
            ),
        ]

        with patch(
            "app.agents.agent4_compliance.retrieve_standards",
            return_value=dummy_chunks,
        ):
            geometry = self._make_geometry()
            material = self._make_material()
            requirements = self._make_requirements()
            state = _make_agent_state(
                geometry=geometry,
                material=material,
                requirements=requirements,
            )
            result = normative_inspector_node(state)

        comp: ComplianceReport = result["compliance"]
        assert comp.retrieved_standards == [c.chunk_text for c in dummy_chunks]
        assert set(comp.standards_referenced) == {"DIN 2095", "ASTM A125"}

        narrative = result["messages"][0].content
        assert "Referenced standards" in narrative or "Standards consulted" in narrative
        assert "DIN 2095" in narrative

    def test_retrieval_miss_falls_back_gracefully(self) -> None:
        """
        Cuando retrieve_standards() no encuentra nada (o el store esta
        vacio), el compliance report se construye igual, sin standards
        citados, y sin fallar el pipeline.
        """
        self._mock_tool.invoke.return_value = json.dumps({
            "status": "ok",
            "report": {
                "approved": True,
                "safety_factor_shear": 2.1,
                "safety_factor_buckling": 1.8,
                "safety_factor_fatigue": None,
                "spring_index": 8.0,
                "wahl_factor": 1.184,
                "corrected_shear_stress_mpa": 320.5,
                "slenderness_ratio": 2.14,
                "applicable_standard": "DIN 2095 / ASTM A125",
                "failure_modes": [],
                "redesign_directives": [],
            },
        })

        from app.agents.agent4_compliance import normative_inspector_node

        with patch(
            "app.agents.agent4_compliance.retrieve_standards",
            return_value=[],
        ):
            geometry = self._make_geometry()
            material = self._make_material()
            requirements = self._make_requirements()
            state = _make_agent_state(
                geometry=geometry,
                material=material,
                requirements=requirements,
            )
            result = normative_inspector_node(state)

        comp: ComplianceReport = result["compliance"]
        assert comp.retrieved_standards == []
        assert comp.standards_referenced == []
        assert comp.approved is True
        assert result["current_step"] == "normative_approved"


# ─────────────────────────────────────────────────────────────────────────────
# Agent 5 — Commercial Optimiser
# ─────────────────────────────────────────────────────────────────────────────


class TestAgent5Commercial:
    """Tests para agent5_commercial.commercial_optimiser_node."""

    @pytest.fixture(autouse=True)
    def _patch_tool(self, request: pytest.FixtureRequest) -> None:
        """Parchea commercial_scoring_tool.invoke."""
        patcher = patch(
            "app.agents.agent5_commercial.commercial_scoring_tool"
        )
        self._mock_tool = patcher.start()
        request.addfinalizer(patcher.stop)

    @pytest.fixture(autouse=True)
    def _patch_geo_tool(self, request: pytest.FixtureRequest) -> None:
        """Parchea calculate_spring_geometry_tool.invoke (alternativas)."""
        patcher = patch(
            "app.agents.agent5_commercial.calculate_spring_geometry_tool"
        )
        self._mock_geo_tool = patcher.start()
        request.addfinalizer(patcher.stop)

    @pytest.fixture(autouse=True)
    def _patch_comp_tool(self, request: pytest.FixtureRequest) -> None:
        """Parchea compliance_verification_tool.invoke (alternativas)."""
        patcher = patch(
            "app.agents.agent5_commercial.compliance_verification_tool"
        )
        self._mock_comp_tool = patcher.start()
        request.addfinalizer(patcher.stop)

    def _make_geometry(self) -> SpringGeometry:
        return SpringGeometry(
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

    def _make_material(self) -> MaterialProperties:
        return MaterialProperties(
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

    def _mock_commercial_tool(self) -> None:
        """Configura el mock de scoring tool con datos controlados."""
        self._mock_tool.invoke.return_value = json.dumps({
            "status": "ok",
            "ranked_proposals": [
                {
                    "proposal_id": "P001",
                    "rank": 1,
                    "composite_score": 0.85,
                    "wire_mass_kg": 0.012,
                    "material_cost_usd": 0.0456,
                    "estimated_life_cycles": 1_050_000,
                    "three_js_params": {
                        "wireRadius": 1.75,
                        "coilRadius": 14.0,
                        "totalCoils": 10.0,
                        "height": 60.0,
                        "tubeSegments": 64,
                        "radialSegments": 16,
                    },
                },
            ],
            "chart_data": [
                {
                    "proposal_id": "P001",
                    "rank": 1,
                    "composite_score": 0.85,
                    "material_cost_usd": 0.0456,
                    "estimated_life_cycles": 1_050_000,
                    "safety_factor_shear": 2.1,
                    "safety_factor_buckling": 1.8,
                    "wire_mass_kg": 0.012,
                },
            ],
        })

    def test_valid_state_returns_proposals(self) -> None:
        """
        Verifica que con state valido (geometry + material), el agente
        retorna proposals comerciales y final_report.
        """
        self._mock_commercial_tool()

        from app.agents.agent5_commercial import commercial_optimiser_node

        geometry = self._make_geometry()
        material = self._make_material()
        compliance = ComplianceReport(
            approved=True,
            safety_factor_shear=2.1,
            safety_factor_buckling=1.8,
            safety_factor_fatigue=None,
            applicable_standard="DIN 2095",
            failure_modes=[],
            redesign_directives=[],
        )
        requirements = UserRequirements(
            raw_input="Design a spring",
            spring_type="compression",
            load_force_n=100.0,
            deflection_mm=20.0,
        )
        state = _make_agent_state(
            geometry=geometry,
            material=material,
            compliance=compliance,
            requirements=requirements,
        )
        result = commercial_optimiser_node(state)

        assert "commercial_proposals" in result
        assert len(result["commercial_proposals"]) == 1
        assert result["commercial_proposals"][0].rank == 1
        assert result["commercial_proposals"][0].composite_score == 0.85

        assert "final_report" in result
        report = result["final_report"]
        assert report["summary"]["spring_type"] == "compression"
        assert report["summary"]["material"] == "ASTM A228 Music Wire"
        assert report["summary"]["approved"] is True
        assert "commercial" in report
        assert "three_js_scene" in report

    def test_missing_geometry_returns_error(self) -> None:
        """
        Verifica que cuando geometry es None, el agente retorna error.
        """
        from app.agents.agent5_commercial import commercial_optimiser_node

        material = self._make_material()
        state = _make_agent_state(geometry=None, material=material)
        result = commercial_optimiser_node(state)

        assert "commercial_proposals" not in result
        assert result["errors"][0]["error_type"] == "MissingDependencies"

    def test_missing_material_returns_error(self) -> None:
        """
        Verifica que cuando material es None, el agente retorna error.
        """
        from app.agents.agent5_commercial import commercial_optimiser_node

        geometry = self._make_geometry()
        state = _make_agent_state(geometry=geometry, material=None)
        result = commercial_optimiser_node(state)

        assert "commercial_proposals" not in result
        assert result["errors"][0]["error_type"] == "MissingDependencies"

    def test_tool_error_returns_error(self) -> None:
        """
        Verifica que cuando la herramienta retorna error, el agente
        propaga el error.
        """
        self._mock_tool.invoke.return_value = json.dumps({
            "status": "error",
            "message": "Scoring failed",
        })

        from app.agents.agent5_commercial import commercial_optimiser_node

        geometry = self._make_geometry()
        material = self._make_material()
        compliance = ComplianceReport(
            approved=True,
            safety_factor_shear=2.1,
            safety_factor_buckling=1.8,
            safety_factor_fatigue=None,
            applicable_standard="DIN 2095",
            failure_modes=[],
            redesign_directives=[],
        )
        requirements = UserRequirements(
            raw_input="Design a spring",
            spring_type="compression",
            load_force_n=100.0,
            deflection_mm=20.0,
        )
        state = _make_agent_state(
            geometry=geometry,
            material=material,
            compliance=compliance,
            requirements=requirements,
        )
        result = commercial_optimiser_node(state)

        assert "commercial_proposals" not in result
        assert result["errors"][0]["error_type"] == "ToolError"

    def test_tool_exception_returns_error(self) -> None:
        """
        Verifica que cuando la herramienta lanza una excepcion,
        el agente la captura.
        """
        self._mock_tool.invoke.side_effect = ValueError("Bad data")

        from app.agents.agent5_commercial import commercial_optimiser_node

        geometry = self._make_geometry()
        material = self._make_material()
        compliance = ComplianceReport(
            approved=True,
            safety_factor_shear=2.1,
            safety_factor_buckling=1.8,
            safety_factor_fatigue=None,
            applicable_standard="DIN 2095",
            failure_modes=[],
            redesign_directives=[],
        )
        requirements = UserRequirements(
            raw_input="Design a spring",
            spring_type="compression",
            load_force_n=100.0,
            deflection_mm=20.0,
        )
        state = _make_agent_state(
            geometry=geometry,
            material=material,
            compliance=compliance,
            requirements=requirements,
        )
        result = commercial_optimiser_node(state)

        assert "commercial_proposals" not in result
        assert result["errors"][0]["error_type"] == "ValueError"

    # ── Multiple material options (alternatives evaluation) ──────────────

    def _make_alt_material(self) -> MaterialProperties:
        return MaterialProperties(
            material_id=5,
            name="ASTM A401 Chrome-Silicon (SAE 9254)",
            shear_modulus_gpa=77.2,
            elastic_modulus_gpa=200.0,
            density_kg_m3=7850.0,
            yield_strength_mpa=1720.0,
            ultimate_strength_mpa=2000.0,
            max_temp_c=245.0,
            corrosion_resistant=False,
            cost_usd_per_kg=5.60,
        )

    def _mock_geometry_tool_ok(self) -> None:
        """Configura el mock del geometry tool para las alternativas."""
        self._mock_geo_tool.invoke.return_value = json.dumps({
            "status": "ok",
            "geometry": {
                "wire_diameter_mm": 3.2,
                "mean_coil_diameter_mm": 26.0,
                "outer_diameter_mm": 29.2,
                "inner_diameter_mm": 22.8,
                "active_coils": 7.5,
                "total_coils": 9.5,
                "free_length_mm": 55.0,
                "pitch_mm": 7.0,
                "spring_index": 8.125,
                "spring_rate_n_mm": 5.0,
                "torsion_moment_n_mm": None,
                "angular_deflection_deg": None,
            },
        })

    def _mock_compliance_tool(self, approved: bool = True) -> None:
        """Configura el mock del compliance tool para las alternativas."""
        self._mock_comp_tool.invoke.return_value = json.dumps({
            "status": "ok",
            "report": {
                "approved": approved,
                "safety_factor_shear": 1.9,
                "safety_factor_buckling": 1.6,
                "safety_factor_fatigue": None,
                "spring_index": 8.125,
                "wahl_factor": 1.18,
                "corrected_shear_stress_mpa": 350.0,
                "slenderness_ratio": 2.1,
                "applicable_standard": "DIN 2095 / ASTM A125",
                "failure_modes": [] if approved else [
                    "Insufficient shear safety factor: 1.100 < 1.30"
                ],
                "redesign_directives": [],
            },
        })

    def _mock_commercial_tool_multi(self) -> None:
        """Mock de scoring con 2 propuestas: P002 gana el score (rank 1)."""
        three_js = {
            "wireRadius": 1.75,
            "coilRadius": 14.0,
            "totalCoils": 10.0,
            "height": 60.0,
            "tubeSegments": 64,
            "radialSegments": 16,
        }
        self._mock_tool.invoke.return_value = json.dumps({
            "status": "ok",
            "ranked_proposals": [
                {
                    "proposal_id": "P002",
                    "rank": 1,
                    "composite_score": 0.91,
                    "wire_mass_kg": 0.010,
                    "material_cost_usd": 0.0560,
                    "estimated_life_cycles": 950_000,
                    "three_js_params": three_js,
                },
                {
                    "proposal_id": "P001",
                    "rank": 2,
                    "composite_score": 0.85,
                    "wire_mass_kg": 0.012,
                    "material_cost_usd": 0.0456,
                    "estimated_life_cycles": 1_050_000,
                    "three_js_params": three_js,
                },
            ],
            "chart_data": [
                {
                    "proposal_id": "P002",
                    "rank": 1,
                    "composite_score": 0.91,
                    "material_cost_usd": 0.0560,
                    "estimated_life_cycles": 950_000,
                    "safety_factor_shear": 1.9,
                    "safety_factor_buckling": 1.6,
                    "wire_mass_kg": 0.010,
                },
                {
                    "proposal_id": "P001",
                    "rank": 2,
                    "composite_score": 0.85,
                    "material_cost_usd": 0.0456,
                    "estimated_life_cycles": 1_050_000,
                    "safety_factor_shear": 2.1,
                    "safety_factor_buckling": 1.8,
                    "wire_mass_kg": 0.012,
                },
            ],
        })

    def _make_multi_state(
        self,
        candidates: list[MaterialProperties] | None = None,
    ) -> AgentState:
        """State completo con material primario aprobado + candidatos."""
        geometry = self._make_geometry()
        material = self._make_material()
        compliance = ComplianceReport(
            approved=True,
            safety_factor_shear=2.1,
            safety_factor_buckling=1.8,
            safety_factor_fatigue=None,
            applicable_standard="DIN 2095",
            failure_modes=[],
            redesign_directives=[],
        )
        requirements = UserRequirements(
            raw_input="Design a spring",
            spring_type="compression",
            load_force_n=100.0,
            deflection_mm=20.0,
        )
        overrides: dict[str, object] = {
            "geometry": geometry,
            "material": material,
            "compliance": compliance,
            "requirements": requirements,
        }
        if candidates is not None:
            overrides["material_candidates"] = candidates
        return _make_agent_state(**overrides)

    def test_viable_alternative_becomes_option(self) -> None:
        """
        Verifica que una alternativa viable (geometria ok + compliance
        aprobado) se agrega como propuesta P002 y aparece en options.
        """
        self._mock_geometry_tool_ok()
        self._mock_compliance_tool(approved=True)
        self._mock_commercial_tool_multi()

        from app.agents.agent5_commercial import commercial_optimiser_node

        state = self._make_multi_state(
            candidates=[self._make_material(), self._make_alt_material()],
        )
        result = commercial_optimiser_node(state)

        # El scoring recibio 2 propuestas (P001 + P002)
        scoring_input = json.loads(
            self._mock_tool.invoke.call_args[0][0]["proposals"]
        )
        assert [p["proposal_id"] for p in scoring_input] == ["P001", "P002"]
        # P002 usa las propiedades del material alternativo
        assert scoring_input[1]["cost_usd_per_kg"] == 5.60

        options = result["final_report"]["commercial"]["options"]
        assert len(options) == 2

        by_id = {o["proposal_id"]: o for o in options}
        # El primario sigue siendo el recomendado aunque no gane el rank
        assert by_id["P001"]["is_recommended"] is True
        assert by_id["P001"]["rank"] == 2
        assert by_id["P002"]["is_recommended"] is False
        assert by_id["P002"]["rank"] == 1
        assert by_id["P002"]["material"]["name"] == (
            "ASTM A401 Chrome-Silicon (SAE 9254)"
        )
        assert by_id["P002"]["geometry"]["wire_diameter_mm"] == 3.2
        assert by_id["P002"]["compliance"]["approved"] is True
        # Campos comerciales presentes
        for field in (
            "wire_mass_kg", "material_cost_usd",
            "estimated_life_cycles", "composite_score",
        ):
            assert field in by_id["P002"]

    def test_non_compliant_alternative_excluded(self) -> None:
        """
        Verifica que una alternativa cuya compliance falla NO se convierte
        en propuesta: el scoring recibe solo P001.
        """
        self._mock_geometry_tool_ok()
        self._mock_compliance_tool(approved=False)
        self._mock_commercial_tool()

        from app.agents.agent5_commercial import commercial_optimiser_node

        state = self._make_multi_state(
            candidates=[self._make_material(), self._make_alt_material()],
        )
        result = commercial_optimiser_node(state)

        scoring_input = json.loads(
            self._mock_tool.invoke.call_args[0][0]["proposals"]
        )
        assert [p["proposal_id"] for p in scoring_input] == ["P001"]

        options = result["final_report"]["commercial"]["options"]
        assert len(options) == 1
        assert options[0]["proposal_id"] == "P001"
        assert options[0]["is_recommended"] is True

    def test_alternative_evaluation_exception_is_skipped(self) -> None:
        """
        Verifica que una excepcion al evaluar una alternativa se ignora
        (la alternativa se descarta) sin romper el reporte final.
        """
        self._mock_geo_tool.invoke.side_effect = RuntimeError("optimizer crashed")
        self._mock_commercial_tool()

        from app.agents.agent5_commercial import commercial_optimiser_node

        state = self._make_multi_state(
            candidates=[self._make_material(), self._make_alt_material()],
        )
        result = commercial_optimiser_node(state)

        assert "errors" not in result
        assert "final_report" in result
        options = result["final_report"]["commercial"]["options"]
        assert len(options) == 1
        assert options[0]["proposal_id"] == "P001"

    def test_no_candidates_behaves_like_single_material_path(self) -> None:
        """
        Verifica que sin material_candidates (key absent) el comportamiento
        es identico al actual: una sola propuesta, sin llamadas a
        geometry/compliance.
        """
        self._mock_commercial_tool()

        from app.agents.agent5_commercial import commercial_optimiser_node

        state = self._make_multi_state(candidates=None)
        result = commercial_optimiser_node(state)

        self._mock_geo_tool.invoke.assert_not_called()
        self._mock_comp_tool.invoke.assert_not_called()

        scoring_input = json.loads(
            self._mock_tool.invoke.call_args[0][0]["proposals"]
        )
        assert [p["proposal_id"] for p in scoring_input] == ["P001"]
        assert len(result["commercial_proposals"]) == 1

        options = result["final_report"]["commercial"]["options"]
        assert len(options) == 1
        assert options[0]["is_recommended"] is True

    def test_empty_candidates_list_behaves_like_single_material_path(self) -> None:
        """
        Verifica que con material_candidates=[] explícito el comportamiento
        es idéntico al path de un solo material.
        """
        self._mock_commercial_tool()

        from app.agents.agent5_commercial import commercial_optimiser_node

        state = self._make_multi_state(candidates=[])
        result = commercial_optimiser_node(state)

        self._mock_geo_tool.invoke.assert_not_called()
        self._mock_comp_tool.invoke.assert_not_called()

        scoring_input = json.loads(
            self._mock_tool.invoke.call_args[0][0]["proposals"]
        )
        assert [p["proposal_id"] for p in scoring_input] == ["P001"]
        assert len(result["commercial_proposals"]) == 1

        options = result["final_report"]["commercial"]["options"]
        assert len(options) == 1
        assert options[0]["is_recommended"] is True

    def test_single_candidate_equals_selected_no_alternatives(self) -> None:
        """
        Verifica que cuando la short-list solo contiene el material
        seleccionado, no se evalua ninguna alternativa.
        """
        self._mock_commercial_tool()

        from app.agents.agent5_commercial import commercial_optimiser_node

        state = self._make_multi_state(candidates=[self._make_material()])
        result = commercial_optimiser_node(state)

        self._mock_geo_tool.invoke.assert_not_called()
        self._mock_comp_tool.invoke.assert_not_called()
        assert len(result["final_report"]["commercial"]["options"]) == 1

    def test_multi_option_with_null_compliance_no_crash(self) -> None:
        """
        Verifica que cuando compliance es None en el estado (P001 sin
        ComplianceReport), el agente no falla y la opción P001 emite
        compliance: {} en el dump, con P001 marcado como is_recommended.
        """
        self._mock_geometry_tool_ok()
        self._mock_compliance_tool(approved=True)
        self._mock_commercial_tool()

        from app.agents.agent5_commercial import commercial_optimiser_node

        geometry = self._make_geometry()
        material = self._make_material()
        requirements = UserRequirements(
            raw_input="Design a spring",
            spring_type="compression",
            load_force_n=100.0,
            deflection_mm=20.0,
        )
        # compliance intentionally None
        state = _make_agent_state(
            geometry=geometry,
            material=material,
            compliance=None,
            requirements=requirements,
            material_candidates=[self._make_material(), self._make_alt_material()],
        )
        result = commercial_optimiser_node(state)

        # Must not crash
        assert "final_report" in result

        options = result["final_report"]["commercial"]["options"]
        by_id = {o["proposal_id"]: o for o in options}

        # P001 present and marked recommended
        assert "P001" in by_id
        assert by_id["P001"]["is_recommended"] is True

        # compliance for P001 is an empty dict (compliance was None)
        assert by_id["P001"]["compliance"] == {}

    def test_backward_compat_report_fields_intact(self) -> None:
        """
        Verifica que geometry/material/compliance de nivel superior en
        final_report siguen siendo los del material recomendado (primario)
        y que ranked_proposals/chart_data cubren todas las opciones.
        """
        self._mock_geometry_tool_ok()
        self._mock_compliance_tool(approved=True)
        self._mock_commercial_tool_multi()

        from app.agents.agent5_commercial import commercial_optimiser_node

        state = self._make_multi_state(
            candidates=[self._make_material(), self._make_alt_material()],
        )
        result = commercial_optimiser_node(state)

        report = result["final_report"]
        # Los campos de nivel superior corresponden al primario (P001)
        assert report["geometry"] == self._make_geometry().model_dump()
        assert report["material"] == self._make_material().model_dump()
        assert report["summary"]["material"] == "ASTM A228 Music Wire"
        # ranked_proposals y chart_data cubren TODAS las opciones
        assert len(report["commercial"]["ranked_proposals"]) == 2
        assert len(report["commercial"]["chart_data"]) == 2
        # commercial_proposals (state) tambien incluye ambas
        assert len(result["commercial_proposals"]) == 2
