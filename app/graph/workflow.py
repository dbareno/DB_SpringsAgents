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


def build_spring_design_graph() -> StateGraph:
    """
    Construct and compile the LangGraph StateGraph for spring design.

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

    compiled = builder.compile()
    logger.info("[Graph] Spring design graph compiled successfully.")
    return compiled


# ─────────────────────────────────────────────────────────────────────────────
# Module-level compiled graph (singleton for API reuse)
# ─────────────────────────────────────────────────────────────────────────────

spring_design_graph = build_spring_design_graph()
