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
        "max_free_length_mm": None,
        "solid_length_mm": None,
        "operating_temperature_c": None,
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
        assert len(req.clarification_questions) == 2
        assert "load force" in req.clarification_questions[0].lower()

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
    """Tests para agent3_materials.materials_engineer_node."""

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

    def _make_requirements(self) -> UserRequirements:
        return UserRequirements(
            raw_input="Design a compression spring",
            spring_type="compression",
            load_force_n=100.0,
            deflection_mm=20.0,
            operating_temperature_c=25.0,
            corrosion_resistant=False,
            cyclic_load=False,
        )

    def test_single_candidate_skips_llm(self) -> None:
        """
        Verifica que cuando solo hay un candidato, el agente lo selecciona
        sin invocar el LLM.
        """
        self._mock_tool.invoke.return_value = json.dumps({
            "status": "ok",
            "candidates": self.SINGLE_CANDIDATE,
        })

        from app.agents.agent3_materials import materials_engineer_node

        state = _make_agent_state(requirements=self._make_requirements())
        result = materials_engineer_node(state)

        # No debe llamar al LLM
        self._mock_get_factory.assert_not_called()

        assert "material" in result
        mat: MaterialProperties = result["material"]
        assert isinstance(mat, MaterialProperties)
        assert mat.name == "ASTM A228 Music Wire"
        assert mat.material_id == 1

    def test_multiple_candidates_uses_llm(self) -> None:
        """
        Verifica que con multiples candidatos, el agente invoca el LLM
        para seleccionar el mejor material.
        """
        self._mock_tool.invoke.return_value = json.dumps({
            "status": "ok",
            "candidates": self.MULTIPLE_CANDIDATES,
        })

        llm_response = json.dumps({
            "selected_material_id": 3,
            "justification": "Stainless steel offers corrosion resistance.",
        })
        llm = _make_llm_mock(llm_response)
        factory = _make_factory_mock(llm)
        self._mock_get_factory.return_value = factory

        from app.agents.agent3_materials import materials_engineer_node

        state = _make_agent_state(requirements=self._make_requirements())
        result = materials_engineer_node(state)

        # Debe haber llamado al LLM
        self._mock_get_factory.assert_called()

        assert "material" in result
        mat: MaterialProperties = result["material"]
        assert mat.material_id == 3
        assert mat.name == "ASTM A313 Type 302 Stainless Steel"

    def test_no_matching_material_returns_error(self) -> None:
        """
        Verifica que cuando la herramienta retorna no_match, el agente
        retorna un error.
        """
        self._mock_tool.invoke.return_value = json.dumps({
            "status": "no_match",
            "message": "No material satisfies all constraints.",
        })

        from app.agents.agent3_materials import materials_engineer_node

        state = _make_agent_state(requirements=self._make_requirements())
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

        state = _make_agent_state(requirements=self._make_requirements())
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

        state = _make_agent_state(requirements=self._make_requirements())
        result = materials_engineer_node(state)

        assert "material" not in result
        assert result["errors"][0]["error_type"] == "RuntimeError"


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
