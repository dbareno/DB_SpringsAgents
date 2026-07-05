"""
tests/test_workflow.py
─────────────────────────────────────────────────────────────────────────────
Tests para el grafo de LangGraph (workflow) del Spring Design Agent.

Se verifica que el grafo se compila correctamente, que el estado inicial
tiene la estructura esperada, y que las funciones de ruteo condicional
retornan las rutas correctas segun el estado.
"""

from __future__ import annotations

import pytest

from app.schemas.state import (
    AgentState,
    ComplianceReport,
    MaterialProperties,
    UserRequirements,
    initial_state,
)


# ─────────────────────────────────────────────────────────────────────────────
# Graph compilation
# ─────────────────────────────────────────────────────────────────────────────


class TestGraphCompilation:
    """Tests para la compilacion del grafo de LangGraph."""

    def test_build_spring_design_graph_returns_compiled_graph(self) -> None:
        """
        Verifica que build_spring_design_graph() retorna un grafo compilado
        listo para invocar.
        """
        from app.graph.workflow import build_spring_design_graph

        graph = build_spring_design_graph()

        # El grafo compilado debe tener un nombre y metodo invoke
        assert graph is not None
        assert hasattr(graph, "invoke")
        assert hasattr(graph, "get_graph")

    def test_compiled_graph_has_expected_nodes(self) -> None:
        """
        Verifica que el grafo compilado contiene todos los nodos esperados
        segun la topologia definida en workflow.py.
        """
        from app.graph.workflow import build_spring_design_graph

        graph = build_spring_design_graph()
        # Obtener los nombres de nodos del grafo compilado
        graph_def = graph.get_graph()
        # En LangGraph >=0.2, graph_def.nodes es un dict de nodo_id -> Node
        # o simplemente una lista de strings con los nombres
        node_names: set[str] = set()
        for n in graph_def.nodes:
            if isinstance(n, str):
                node_names.add(n)
            elif hasattr(n, "id"):
                node_names.add(n.id)
            else:
                node_names.add(str(n))

        expected_nodes = {
            "requirements_analyst",
            "materials_engineer",
            "design_engineer",
            "normative_inspector",
            "commercial_optimiser",
            "clarification",
            "increment_iteration",
            "iteration_limit",
            "error_terminal",
        }
        for node in expected_nodes:
            assert node in node_names, (
                f"Nodo esperado '{node}' no encontrado en el grafo"
            )

    def test_compiled_graph_starts_at_requirements_analyst(self) -> None:
        """
        Verifica que el primer nodo del grafo es requirements_analyst
        (la arista START apunta a el).
        """
        from app.graph.workflow import build_spring_design_graph

        graph = build_spring_design_graph()
        graph_def = graph.get_graph()

        # Verificar que la arista START existe
        assert hasattr(graph_def, "edges")
        # START debe conectar a requirements_analyst.
        # En LangGraph las edges pueden ser objetos con .source/.target
        # o tuplas (source, target).
        start_edges = []
        for e in graph_def.edges:
            if isinstance(e, tuple):
                source = e[0]
                target = e[1]
            else:
                source = getattr(e, "source", "")
                target = getattr(e, "target", "")
            if source == "__start__":
                start_edges.append(target)

        assert len(start_edges) > 0
        assert start_edges[0] == "requirements_analyst"

    def test_module_level_graph_is_compiled(self) -> None:
        """
        Verifica que el grafo singleton a nivel de modulo esta compilado.
        """
        from app.graph.workflow import spring_design_graph

        assert spring_design_graph is not None
        assert hasattr(spring_design_graph, "invoke")


# ─────────────────────────────────────────────────────────────────────────────
# Initial state factory
# ─────────────────────────────────────────────────────────────────────────────


class TestInitialState:
    """Tests para la funcion initial_state()."""

    def test_returns_dict_with_expected_keys(self) -> None:
        """
        Verifica que initial_state retorna un diccionario con todas las
        claves definidas en AgentState.
        """
        state = initial_state("Design a compression spring")

        expected_keys = {
            "messages",
            "current_step",
            "iteration_count",
            "max_iterations",
            "requirements",
            "geometry",
            "material",
            "material_candidates",
            "compliance",
            "commercial_proposals",
            "llm_status",
            "errors",
            "final_report",
            "_raw_input",
            "redesign_directives",
            "min_yield_strength_mpa",
            "interrupted",
            "session_answers",
        }
        assert set(state.keys()) == expected_keys

    def test_includes_raw_input(self) -> None:
        """
        Verifica que el input del usuario se almacena en _raw_input.
        """
        state = initial_state("Design a spring for 100N")
        assert state["_raw_input"] == "Design a spring for 100N"

    def test_default_max_iterations(self) -> None:
        """
        Verifica que max_iterations usa el valor por defecto (5).
        """
        state = initial_state("Design a spring")
        assert state["max_iterations"] == 5

    def test_custom_max_iterations(self) -> None:
        """
        Verifica que se puede pasar un valor personalizado de
        max_iterations.
        """
        state = initial_state("Design a spring", max_iterations=3)
        assert state["max_iterations"] == 3

    def test_initial_state_empty_messages(self) -> None:
        """Verifica que el estado inicial tiene mensajes vacios."""
        state = initial_state("Design a spring")
        assert state["messages"] == []

    def test_initial_current_step_is_start(self) -> None:
        """Verifica que current_step comienza como 'start'."""
        state = initial_state("Design a spring")
        assert state["current_step"] == "start"

    def test_initial_iteration_count_is_zero(self) -> None:
        """Verifica que iteration_count comienza en 0."""
        state = initial_state("Design a spring")
        assert state["iteration_count"] == 0

    def test_initial_errors_empty(self) -> None:
        """Verifica que errors comienza como lista vacia."""
        state = initial_state("Design a spring")
        assert state["errors"] == []

    def test_initial_final_report_none(self) -> None:
        """Verifica que final_report comienza como None."""
        state = initial_state("Design a spring")
        assert state["final_report"] is None

    def test_initial_llm_status(self) -> None:
        """
        Verifica que llm_status es una instancia de LLMProviderStatus.
        """
        state = initial_state("Design a spring")
        from app.schemas.state import LLMProviderStatus

        assert isinstance(state["llm_status"], LLMProviderStatus)
        assert state["llm_status"].active_provider == "ollama"
        assert state["llm_status"].failed_providers == []


# ─────────────────────────────────────────────────────────────────────────────
# Route after requirements
# ─────────────────────────────────────────────────────────────────────────────


class TestRouteAfterRequirements:
    """Tests para route_after_requirements()."""

    @pytest.fixture
    def router(self):
        """Importa la funcion de ruteo."""
        from app.agents.agent6_orchestrator import route_after_requirements

        return route_after_requirements

    def test_complete_requirements_returns_design_loop(
        self, router
    ) -> None:
        """
        Verifica que cuando requirements tiene is_complete=True,
        la ruta retorna 'design_loop'.
        """
        requirements = UserRequirements(
            raw_input="Design a spring",
            load_force_n=100.0,
            deflection_mm=20.0,
            is_complete=True,
        )
        state = AgentState({
            "current_step": "requirements_analyst",
            "requirements": requirements,
            "iteration_count": 0,
        })
        result = router(state)
        assert result == "design_loop"

    def test_incomplete_requirements_returns_clarification(
        self, router
    ) -> None:
        """
        Verifica que cuando requirements tiene is_complete=False,
        la ruta retorna 'needs_clarification'.
        """
        requirements = UserRequirements(
            raw_input="I need a spring",
            load_force_n=None,
            deflection_mm=None,
            is_complete=False,
            clarification_questions=["What force?"],
        )
        state = AgentState({
            "current_step": "requirements_analyst",
            "requirements": requirements,
        })
        result = router(state)
        assert result == "needs_clarification"

    def test_missing_requirements_returns_error(self, router) -> None:
        """
        Verifica que cuando requirements es None, la ruta retorna 'error'.
        """
        state = AgentState({
            "current_step": "requirements_analyst",
            "requirements": None,
        })
        result = router(state)
        assert result == "error"

    def test_failed_step_returns_error(self, router) -> None:
        """
        Verifica que cuando current_step contiene 'failed', la ruta
        retorna 'error'.
        """
        state = AgentState({
            "current_step": "requirements_analyst_failed",
            "requirements": None,
        })
        result = router(state)
        assert result == "error"


# ─────────────────────────────────────────────────────────────────────────────
# Route after materials
# ─────────────────────────────────────────────────────────────────────────────


class TestRouteAfterMaterials:
    """Tests para route_after_materials()."""

    @pytest.fixture
    def router(self):
        """Importa la funcion de ruteo."""
        from app.agents.agent6_orchestrator import route_after_materials

        return route_after_materials

    def test_material_selected_returns_ok(self, router) -> None:
        """
        Verifica que cuando Agent 3 selecciona un material, la ruta
        retorna 'ok' → design engineer.
        """
        material = MaterialProperties(
            material_id=1,
            name="ASTM A228 Music Wire",
            shear_modulus_gpa=81.5,
            elastic_modulus_gpa=207.0,
            density_kg_m3=7850,
            yield_strength_mpa=1580,
            ultimate_strength_mpa=1900,
            max_temp_c=120,
            corrosion_resistant=False,
            cost_usd_per_kg=3.80,
        )
        state = AgentState({
            "current_step": "materials_engineer",
            "material": material,
        })
        result = router(state)
        assert result == "ok"

    def test_failed_step_returns_error(self, router) -> None:
        """
        Verifica que cuando current_step contiene 'failed' (p.ej.
        NoMaterialMatch), la ruta retorna 'error' SIN pasar por Agent 2.
        """
        state = AgentState({
            "current_step": "materials_engineer_failed",
            "material": None,
        })
        result = router(state)
        assert result == "error"

    def test_missing_material_returns_error(self, router) -> None:
        """
        Verifica que cuando material es None (incluso sin 'failed' en el
        step), la ruta retorna 'error' como red de seguridad.
        """
        state = AgentState({
            "current_step": "materials_engineer",
            "material": None,
        })
        result = router(state)
        assert result == "error"


# ─────────────────────────────────────────────────────────────────────────────
# Route after compliance
# ─────────────────────────────────────────────────────────────────────────────


class TestRouteAfterCompliance:
    """Tests para route_after_compliance()."""

    @pytest.fixture
    def router(self):
        """Importa la funcion de ruteo."""
        from app.agents.agent6_orchestrator import route_after_compliance

        return route_after_compliance

    def test_approved_returns_commercial(self, router) -> None:
        """
        Verifica que cuando compliance.approved=True, la ruta retorna
        'approved' → commercial optimiser.
        """
        compliance = ComplianceReport(
            approved=True,
            safety_factor_shear=2.1,
            safety_factor_buckling=1.8,
            safety_factor_fatigue=None,
            applicable_standard="DIN 2095",
            failure_modes=[],
            redesign_directives=[],
        )
        state = AgentState({
            "current_step": "normative_approved",
            "compliance": compliance,
            "iteration_count": 0,
            "max_iterations": 5,
        })
        result = router(state)
        assert result == "approved"

    def test_rejected_within_limit_returns_redesign(self, router) -> None:
        """
        Verifica que cuando compliance.approved=False y aun hay
        iteraciones disponibles, la ruta retorna 'redesign'.
        """
        compliance = ComplianceReport(
            approved=False,
            safety_factor_shear=1.1,
            safety_factor_buckling=1.8,
            safety_factor_fatigue=None,
            applicable_standard="DIN 2095",
            failure_modes=["Shear safety too low"],
            redesign_directives=["Increase wire diameter"],
        )
        state = AgentState({
            "current_step": "redesign_needed",
            "compliance": compliance,
            "iteration_count": 1,
            "max_iterations": 5,
        })
        result = router(state)
        assert result == "redesign"

    def test_rejected_at_limit_returns_iteration_limit(
        self, router
    ) -> None:
        """
        Verifica que cuando compliance.approved=False y se alcanzo
        el maximo de iteraciones, la ruta retorna 'iteration_limit'.
        """
        compliance = ComplianceReport(
            approved=False,
            safety_factor_shear=1.1,
            safety_factor_buckling=1.8,
            safety_factor_fatigue=None,
            applicable_standard="DIN 2095",
            failure_modes=["Shear safety too low"],
            redesign_directives=["Increase wire diameter"],
        )
        state = AgentState({
            "current_step": "redesign_needed",
            "compliance": compliance,
            "iteration_count": 5,
            "max_iterations": 5,
        })
        result = router(state)
        assert result == "iteration_limit"

    def test_missing_compliance_returns_error(self, router) -> None:
        """
        Verifica que cuando compliance es None, la ruta retorna 'error'.
        """
        state = AgentState({
            "current_step": "normative_inspector",
            "compliance": None,
            "iteration_count": 0,
            "max_iterations": 5,
        })
        result = router(state)
        assert result == "error"

    def test_failed_step_returns_error(self, router) -> None:
        """
        Verifica que cuando current_step contiene 'failed', la ruta
        retorna 'error'.
        """
        state = AgentState({
            "current_step": "normative_inspector_failed",
            "compliance": None,
        })
        result = router(state)
        assert result == "error"
