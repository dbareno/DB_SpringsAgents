"""
tests/test_conversation_flow.py
─────────────────────────────────────────────────────────────────────────────
Phase 3 — multi-turn conversation via LangGraph checkpointer + interrupt().

Verifies:
  1. Single clarification round (ask → answer → approved-shaped resume).
  2. Multi-round clarification (ask → answer → ask more → answer → complete).
  3. `session_id` (thread_id) stability across rounds.
  4. Checkpoint persistence: state survives a FRESH graph instance built
     from the SAME on-disk SQLite checkpoint file (simulates a process
     restart within the session window).
  5. The redesign loop (Agent 4 rejected → increment_iteration → Agent 3)
     contains NO interrupts — only the requirements_analyst node pauses.

These tests use a MINIMAL graph wrapping ``requirements_analyst_node``
directly (the actual node the interrupt() logic lives in), because
``interrupt()`` requires a real LangGraph runnable context (it calls
``get_config()`` internally) — see ``app/agents/agent1_requirements.py``.
The full 5-agent pipeline's topology-level interrupt/resume behavior is
already covered structurally by ``tests/test_workflow.py``.

Resume contract: answers are passed via ``Command(resume=answers_dict)`` —
matching ``DesignService._resume_graph_and_persist``. LangGraph replays a
node's ENTIRE body from the top on every resume; a call to ``interrupt()``
only pauses the FIRST time that call site is reached fresh — subsequent
replays of an ALREADY-resumed call return the resume value immediately
without re-pausing. This is why ``requirements_analyst_node`` loops per
conversation round (each round = one fresh, not-yet-resumed ``interrupt()``
call at that position), accepting that earlier rounds' LLM calls replay on
each resume (inherent LangGraph cost, not a bug).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from app.agents.agent1_requirements import requirements_analyst_node
from app.agents.agent6_orchestrator import increment_iteration_node, route_after_compliance
from app.schemas.state import AgentState, ComplianceReport, UserRequirements, initial_state


def _build_requirements_graph(checkpointer):
    """One-node graph wrapping the real requirements_analyst_node."""
    builder = StateGraph(AgentState)
    builder.add_node("requirements_analyst", requirements_analyst_node)
    builder.add_edge(START, "requirements_analyst")
    builder.add_edge("requirements_analyst", END)
    return builder.compile(checkpointer=checkpointer)


def _llm_json(**overrides: object) -> str:
    """Build a JSON string matching Agent 1's expected LLM output schema."""
    base = {
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
        "clarification_questions": [],
    }
    base.update(overrides)
    return json.dumps(base)


@pytest.fixture
def mock_factory(request: pytest.FixtureRequest):
    """Patch get_factory in agent1_requirements; controllable per-call responses."""
    patcher = patch("app.agents.agent1_requirements.get_factory")
    mock_get_factory = patcher.start()
    request.addfinalizer(patcher.stop)

    factory = MagicMock()
    factory._priority_order = ["gemini", "openai"]
    llm = MagicMock()
    factory.get_llm.return_value = llm
    mock_get_factory.return_value = factory
    return llm


class TestSingleClarificationRound:
    """Ask once → answer once → requirements complete.

    NOTE: completeness/questions are decided PROGRAMMATICALLY by
    ``_determine_completeness`` via regex extraction over ``raw_input`` (the
    LLM's own ``clarification_questions``/``is_complete`` fields are
    overridden — see agent1_requirements.py). So answers must be phrased in a
    way the regex extractors understand ("120N", "15mm") — exactly what the
    resume path's label-building logic (``_label_for_question``) produces.
    """

    def test_ask_answer_complete(self, mock_factory: MagicMock) -> None:
        # Every LLM call returns the same generic JSON — completeness is
        # regex-driven, not LLM-content-driven (see class docstring), and
        # the exact NUMBER of replay calls is an internal LangGraph detail
        # we don't want this test coupled to.
        mock_factory.invoke.side_effect = lambda *_a, **_k: AIMessage(
            content=_llm_json()
        )

        checkpointer = InMemorySaver()
        graph = _build_requirements_graph(checkpointer)
        session_id = "session-single-round"
        config = {"configurable": {"thread_id": session_id}}

        state = initial_state("I need a spring for a pen")
        result = graph.invoke(state, config)

        assert "__interrupt__" in result, "Graph must pause at interrupt()"
        payload = result["__interrupt__"][0].value
        assert payload["type"] == "clarification_needed"
        questions = payload["questions"]
        assert len(questions) > 0

        # Answer ALL questions so the round completes in one resume.
        answers = dict.fromkeys(questions, "")
        for q in questions:
            lowered = q.lower()
            if "fuerza" in lowered or "carga" in lowered:
                answers[q] = "120N"
            elif "deflexi" in lowered or "recorrido" in lowered:
                answers[q] = "15mm"
            elif "tipo de resorte" in lowered:
                answers[q] = "compresión"

        result2 = graph.invoke(Command(resume=answers), config)

        assert "__interrupt__" not in result2
        req: UserRequirements = result2["requirements"]
        assert req.is_complete is True
        assert req.load_force_n == 120.0
        assert req.deflection_mm == 15.0
        # session_answers accumulated on the state for downstream use.
        assert result2["session_answers"] == answers


class TestMultiRoundClarification:
    """Ask → answer → ask more → answer → complete. thread_id stays constant."""

    def test_two_rounds_then_complete(self, mock_factory: MagicMock) -> None:
        # Content-agnostic (regex-driven completeness) — see class docstring.
        mock_factory.invoke.side_effect = lambda *_a, **_k: AIMessage(
            content=_llm_json()
        )

        checkpointer = InMemorySaver()
        graph = _build_requirements_graph(checkpointer)
        session_id = "session-multi-round"
        config = {"configurable": {"thread_id": session_id}}

        state = initial_state("I need a spring")
        r1 = graph.invoke(state, config)
        assert "__interrupt__" in r1
        q1 = r1["__interrupt__"][0].value["questions"]
        assert len(q1) > 0

        # Round 1: answer force + spring type — deliberately leave
        # deflection unanswered so a SECOND round is still required.
        force_q = next(q for q in q1 if "fuerza" in q.lower() or "carga" in q.lower())
        type_q = next(q for q in q1 if "tipo de resorte" in q.lower())
        r2 = graph.invoke(
            Command(resume={force_q: "120N", type_q: "compresión"}), config
        )
        assert "__interrupt__" in r2, (
            "Second round should still be incomplete (no deflection yet)"
        )
        q2 = r2["__interrupt__"][0].value["questions"]
        # Force/type questions must no longer be pending (they're resolved).
        assert not any("fuerza" in q.lower() or "carga" in q.lower() for q in q2)
        assert not any("tipo de resorte" in q.lower() for q in q2)
        deflection_q = next(
            q for q in q2 if "deflexi" in q.lower() or "recorrido" in q.lower()
        )

        # Round 2: answer deflection → now complete.
        r3 = graph.invoke(Command(resume={deflection_q: "15mm"}), config)
        assert "__interrupt__" not in r3
        req: UserRequirements = r3["requirements"]
        assert req.is_complete is True
        assert req.load_force_n == 120.0
        assert req.deflection_mm == 15.0
        assert req.spring_type == "compression"

        # session_answers accumulates ACROSS rounds (both answer batches present).
        session_answers = r3["session_answers"]
        assert len(session_answers) == 3
        assert session_answers[force_q] == "120N"
        assert session_answers[type_q] == "compresión"
        assert session_answers[deflection_q] == "15mm"

    def test_thread_id_stable_across_rounds(self, mock_factory: MagicMock) -> None:
        """The same thread_id/session_id is reused for every resume call."""
        mock_factory.invoke.side_effect = lambda *_a, **_k: AIMessage(
            content=_llm_json()
        )

        checkpointer = InMemorySaver()
        graph = _build_requirements_graph(checkpointer)
        session_id = "session-stable-id"
        config = {"configurable": {"thread_id": session_id}}

        r1 = graph.invoke(initial_state("small spring"), config)
        question = r1["__interrupt__"][0].value["questions"][0]
        graph.invoke(Command(resume={question: "50N, 10mm"}), config)

        # Only ONE thread exists in the checkpointer's storage.
        state_history = list(graph.get_state_history(config))
        thread_ids = {
            snap.config["configurable"]["thread_id"] for snap in state_history
        }
        assert thread_ids == {session_id}


class TestCheckpointPersistence:
    """Checkpoint survives being re-opened as a FRESH graph/saver instance."""

    def test_resume_from_freshly_loaded_checkpoint(
        self, mock_factory: MagicMock, tmp_path
    ) -> None:
        """
        Simulates a process restart: build the graph, pause at interrupt,
        then build a BRAND NEW graph instance pointed at the same on-disk
        SQLite checkpoint DB and resume from there — proving the pause
        survived independently of any in-memory Python state.
        """
        from langgraph.checkpoint.sqlite import SqliteSaver

        mock_factory.invoke.side_effect = lambda *_a, **_k: AIMessage(
            content=_llm_json()
        )

        db_path = str(tmp_path / "checkpoints.sqlite")
        session_id = "session-restart-sim"
        config = {"configurable": {"thread_id": session_id}}

        # ── "Process 1": start the conversation, pause at interrupt ────────
        with SqliteSaver.from_conn_string(db_path) as saver1:
            graph1 = _build_requirements_graph(saver1)
            result1 = graph1.invoke(initial_state("need a spring"), config)
            assert "__interrupt__" in result1
            questions = result1["__interrupt__"][0].value["questions"]

        # Answer EVERYTHING needed for completeness in a single round:
        # spring_type + force + deflection.
        answers = {}
        for q in questions:
            lowered = q.lower()
            if "tipo de resorte" in lowered:
                answers[q] = "compresión"
            elif "fuerza" in lowered or "carga" in lowered:
                answers[q] = "80N"
            elif "deflexi" in lowered or "recorrido" in lowered:
                answers[q] = "12mm"

        # ── "Process 2": brand new saver/graph, same DB file, same thread_id ─
        with SqliteSaver.from_conn_string(db_path) as saver2:
            graph2 = _build_requirements_graph(saver2)
            result2 = graph2.invoke(Command(resume=answers), config)

        assert "__interrupt__" not in result2
        req: UserRequirements = result2["requirements"]
        assert req.is_complete is True
        assert req.load_force_n == 80.0


class TestRedesignLoopHasNoInterrupts:
    """
    Acceptance: the redesign loop (Agent 4 rejects → increment_iteration →
    back to Agent 3/materials) must NOT interrupt — only Agent 1's
    requirements node pauses. This loop runs autonomously within a single
    graph invocation.
    """

    def test_increment_iteration_node_returns_plain_dict_no_interrupt(self) -> None:
        """increment_iteration_node must be a normal (non-pausing) node."""
        compliance = ComplianceReport(
            approved=False,
            safety_factor_shear=0.8,
            safety_factor_buckling=1.0,
            applicable_standard="DIN 2095",
            failure_modes=["shear_safety_below_threshold"],
            redesign_directives=["Increase wire diameter"],
        )
        state = AgentState(
            {
                "iteration_count": 1,
                "compliance": compliance,
                "min_yield_strength_mpa": None,
            }
        )

        result = increment_iteration_node(state)

        # Plain dict, no __interrupt__ key, no pausing behavior.
        assert isinstance(result, dict)
        assert "__interrupt__" not in result
        assert result["iteration_count"] == 2
        assert result["geometry"] is None
        assert result["compliance"] is None
        assert result["redesign_directives"] == ["Increase wire diameter"]

    def test_route_after_compliance_redesign_within_limit(self) -> None:
        """Rejected compliance within iteration limit routes to redesign, not clarification."""
        compliance = ComplianceReport(
            approved=False,
            safety_factor_shear=0.8,
            safety_factor_buckling=1.0,
            applicable_standard="DIN 2095",
            failure_modes=["shear_safety_below_threshold"],
        )
        state = AgentState(
            {
                "current_step": "normative_inspector",
                "compliance": compliance,
                "iteration_count": 1,
                "max_iterations": 5,
            }
        )

        assert route_after_compliance(state) == "redesign"
