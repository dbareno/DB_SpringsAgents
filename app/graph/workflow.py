"""
app/graph/workflow.py
─────────────────────────────────────────────────────────────────────────────
LangGraph workflow definition for the Spring Design Agent system.

Graph topology
──────────────

    ┌──────────────────────────────────────────────────────────────────┐
    │                     Spring Design Graph                          │
    │                                                                  │
    │  START ──► requirements_analyst                                  │
    │                    │                                             │
    │       ┌────────────┼────────────┐                               │
    │       ▼            ▼            ▼                               │
    │  clarification   error    materials_engineer                     │
    │  (terminal)   (terminal)       │                                 │
    │                     ┌──────────┴──────────┐                     │
    │                     ▼                     ▼                     │
    │                   error              design_engineer             │
    │                (terminal)                 │                      │
    │                        normative_inspector                       │
    │                                │                                 │
    │               ┌────────────────┼────────────┐                   │
    │               ▼                ▼            ▼                   │
    │          redesign_loop      error       commercial_optimiser     │
    │               │          (terminal)         │                    │
    │     iteration_counter                      END                   │
    │               │                                                  │
    │     ┌─────────┴─────────┐                                       │
    │     ▼                   ▼                                        │
    │  materials_engineer  iteration_limit                             │
    │  (loop back)         (terminal)                                  │
    └──────────────────────────────────────────────────────────────────┘

All nodes are regular Python functions (not async) for simplicity; swap to
async node functions + AsyncSQLiteSaver / AsyncPostgresSaver for production.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from langgraph.graph import END, START, StateGraph

from app.agents.agent1_requirements import requirements_analyst_node
from app.agents.agent2_design import design_engineer_node
from app.agents.agent3_materials import materials_engineer_node
from app.agents.agent4_compliance import normative_inspector_node
from app.agents.agent5_commercial import commercial_optimiser_node
from app.agents.agent6_orchestrator import (
    clarification_node,
    increment_iteration_node,
    iteration_limit_node,
    route_after_compliance,
    route_after_materials,
    route_after_requirements,
)
from app.schemas.state import AgentState

if TYPE_CHECKING:
    from langgraph.checkpoint.base import BaseCheckpointSaver

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Node name constants (single source of truth)
# ─────────────────────────────────────────────────────────────────────────────

N_REQUIREMENTS    = "requirements_analyst"
N_MATERIALS       = "materials_engineer"
N_DESIGN          = "design_engineer"
N_COMPLIANCE      = "normative_inspector"
N_COMMERCIAL      = "commercial_optimiser"
N_CLARIFICATION   = "clarification"
N_INCREMENT       = "increment_iteration"
N_ITER_LIMIT      = "iteration_limit"
N_ERROR           = "error_terminal"


# ─────────────────────────────────────────────────────────────────────────────
# Error terminal node
# ─────────────────────────────────────────────────────────────────────────────

def error_terminal_node(state: AgentState) -> dict:
    """Catches unrecoverable errors and surfaces them in final_report."""
    errors = state.get("errors", [])
    logger.error("[Graph] Error terminal reached. Errors: %s", errors)
    return {
        "current_step": "error_terminal",
        "final_report": {
            "status": "error",
            "message": "An unrecoverable error occurred during design.",
            "errors": errors,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# Graph builder
# ─────────────────────────────────────────────────────────────────────────────


def build_spring_design_graph(
    checkpointer: "BaseCheckpointSaver | None" = None,
) -> StateGraph:
    """
    Construct and compile the LangGraph StateGraph for spring design.

    Graph topology is UNCHANGED from Phase 0-2 — this function only adds
    optional checkpointing (Phase 3) so the graph can be paused at the
    ``requirements_analyst`` node's ``interrupt()`` call and resumed later
    from a persisted checkpoint instead of being re-run from ``START``.

    Parameters
    ----------
    checkpointer:
        Optional ``BaseCheckpointSaver`` (e.g. ``AsyncSqliteSaver``). When
        provided, the compiled graph supports ``interrupt``/``Command(resume=...)``
        multi-turn conversations keyed by ``thread_id``. When omitted (the
        default), the graph behaves exactly as before — a single ``invoke()``
        runs start-to-finish with no persistence, which existing callers and
        tests rely on.

    Returns
    -------
    CompiledGraph
        A compiled LangGraph instance ready to be invoked with an initial
        state dict.

    Example
    -------
    >>> graph = build_spring_design_graph()
    >>> result = graph.invoke(initial_state("I need a compression spring..."))
    """
    builder = StateGraph(AgentState)  # type: ignore[arg-type]

    # ── Register nodes ────────────────────────────────────────────────────
    builder.add_node(N_REQUIREMENTS,  requirements_analyst_node)
    builder.add_node(N_MATERIALS,     materials_engineer_node)
    builder.add_node(N_DESIGN,        design_engineer_node)
    builder.add_node(N_COMPLIANCE,    normative_inspector_node)
    builder.add_node(N_COMMERCIAL,    commercial_optimiser_node)
    builder.add_node(N_CLARIFICATION, clarification_node)
    builder.add_node(N_INCREMENT,     increment_iteration_node)
    builder.add_node(N_ITER_LIMIT,    iteration_limit_node)
    builder.add_node(N_ERROR,         error_terminal_node)

    # ── Entry point ───────────────────────────────────────────────────────
    builder.add_edge(START, N_REQUIREMENTS)

    # ── After Agent 1: conditional routing ───────────────────────────────
    builder.add_conditional_edges(
        N_REQUIREMENTS,
        route_after_requirements,
        {
            "needs_clarification": N_CLARIFICATION,
            "design_loop":         N_MATERIALS,
            "error":               N_ERROR,
        },
    )

    # ── After Agent 3: conditional routing ────────────────────────────
    #    (a NoMaterialMatch / tool error must NOT fall through to Agent 2,
    #    which would silently substitute generic material defaults)
    builder.add_conditional_edges(
        N_MATERIALS,
        route_after_materials,
        {
            "ok":    N_DESIGN,
            "error": N_ERROR,
        },
    )
    builder.add_edge(N_DESIGN, N_COMPLIANCE)

    # ── After Agent 4: conditional routing ────────────────────────────
    builder.add_conditional_edges(
        N_COMPLIANCE,
        route_after_compliance,
        {
            "approved":        N_COMMERCIAL,
            "redesign":        N_INCREMENT,
            "iteration_limit": N_ITER_LIMIT,
            "error":           N_ERROR,
        },
    )

    # ── Redesign loop: increment → back to materials (fresh material
    #    selection may also be needed if geometry constraints changed)
    builder.add_edge(N_INCREMENT, N_MATERIALS)

    # ── Terminal nodes → END ──────────────────────────────────────────
    builder.add_edge(N_COMMERCIAL,    END)
    builder.add_edge(N_CLARIFICATION, END)
    builder.add_edge(N_ITER_LIMIT,    END)
    builder.add_edge(N_ERROR,         END)

    compiled = builder.compile(checkpointer=checkpointer)
    logger.info(
        "[Graph] Spring design graph compiled successfully (checkpointer=%s).",
        "enabled" if checkpointer is not None else "disabled",
    )
    return compiled


# ─────────────────────────────────────────────────────────────────────────────
# Module-level compiled graph (singleton for API reuse)
# ─────────────────────────────────────────────────────────────────────────────
#
# NOTE: this singleton has NO checkpointer — it is kept for backward
# compatibility with call sites / tests that don't need multi-turn resume
# (e.g. exports, one-shot tool tests). ``DesignService`` uses the
# checkpointed graph from ``get_design_graph()`` instead (see
# ``app/services/design_service.py``).

spring_design_graph = build_spring_design_graph()


# ─────────────────────────────────────────────────────────────────────────────
# Checkpointed graph accessor (Phase 3 — multi-turn conversation)
# ─────────────────────────────────────────────────────────────────────────────

_checkpointed_graph: StateGraph | None = None


async def get_design_graph() -> StateGraph:
    """
    Return the process-wide graph compiled WITH the ``AsyncSqliteSaver``
    checkpointer, building it lazily on first call.

    Same topology as :func:`build_spring_design_graph` — this only attaches
    persistence so ``ainvoke(Command(resume=...), config={"configurable":
    {"thread_id": session_id}})`` can resume from an interrupt instead of
    restarting the pipeline.
    """
    global _checkpointed_graph

    if _checkpointed_graph is not None:
        return _checkpointed_graph

    from app.core.checkpointer import get_checkpointer

    checkpointer = await get_checkpointer()
    _checkpointed_graph = build_spring_design_graph(checkpointer=checkpointer)
    return _checkpointed_graph
