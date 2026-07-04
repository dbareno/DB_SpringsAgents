"""
app/agents/agent6_orchestrator.py
─────────────────────────────────────────────────────────────────────────────
Agent 6 – Orchestrator (Router / Supervisor)

This module provides the **conditional edge functions** that LangGraph evaluates
after each node to decide which node to visit next.

Routing logic overview
──────────────────────

  START
    │
    ▼
  [Agent 1: requirements_analyst]
    │
    ├─ is_complete=False → ASK_CLARIFICATION (terminal: return to user)
    ├─ error            → HANDLE_ERROR
    └─ is_complete=True ─────────────────────────────────────────────┐
                                                                     ▼
                                                         [Agent 3: materials_engineer]
                                                                     │
                                                    ┌────────────────┴────────────────┐
                                                    │                                 │
                                              no_material_match              [Agent 2: design_engineer]
                                                    │                                 │
                                              HANDLE_ERROR                            │
                                                         [Agent 4: normative_inspector]
                                                                     │
                                      ┌──────────────────────────────┤
                                      │                              │
                              redesign_needed                 normative_approved
                                      │                              │
                              iteration < max?            [Agent 5: commercial_optimiser]
                                      │                              │
                               YES ───┘                          END ✓
                               NO  → ITERATION_LIMIT

Fallback / LLM quota rotation is handled inside each individual agent node.
The orchestrator's role is purely graph-level routing.
"""

from __future__ import annotations

import logging

from app.schemas.state import AgentState

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Route after Agent 1 (Requirements Analyst)
# ─────────────────────────────────────────────────────────────────────────────


def route_after_requirements(state: AgentState) -> str:
    """
    Decide what to do after the requirements extraction step.

    Returns
    -------
    "needs_clarification"   → graph ends; API returns questions to the user.
    "design_loop"           → proceed to materials + design.
    "error"                 → unrecoverable extraction failure.
    """
    step = state.get("current_step", "")

    if "failed" in step:
        logger.warning("[Router] Requirements analyst failed → error branch.")
        return "error"

    requirements = state.get("requirements")
    if requirements is None:
        logger.warning("[Router] requirements is None → error branch.")
        return "error"

    if not requirements.is_complete:
        logger.info(
            "[Router] Requirements incomplete (%d questions) → clarification.",
            len(requirements.clarification_questions),
        )
        return "needs_clarification"

    logger.info("[Router] Requirements complete → design_loop.")
    return "design_loop"


# ─────────────────────────────────────────────────────────────────────────────
# Route after Agent 3 (Materials Engineer)
# ─────────────────────────────────────────────────────────────────────────────


def route_after_materials(state: AgentState) -> str:
    """
    Decide what to do after material selection.

    Returns
    -------
    "ok"     → a material was selected; proceed to design engineer.
    "error"  → no material satisfies the constraints (or a tool error occurred).
    """
    step = state.get("current_step", "")

    if "failed" in step:
        logger.warning("[Router] Materials engineer failed → error branch.")
        return "error"

    if state.get("material") is None:
        logger.warning("[Router] material is None → error branch.")
        return "error"

    return "ok"


# ─────────────────────────────────────────────────────────────────────────────
# Route after Agent 4 (Normative Inspector)
# ─────────────────────────────────────────────────────────────────────────────


def route_after_compliance(state: AgentState) -> str:
    """
    Decide what to do after the normative compliance check.

    Returns
    -------
    "approved"              → proceed to commercial optimiser.
    "redesign"              → loop back to design engineer (if within limit).
    "iteration_limit"       → too many retries; surface the last error.
    "error"                 → unexpected tool / agent failure.
    """
    step = state.get("current_step", "")
    iteration = state.get("iteration_count", 0)
    max_iter = state.get("max_iterations", 5)

    if "failed" in step:
        logger.warning("[Router] Compliance inspector failed → error branch.")
        return "error"

    compliance = state.get("compliance")
    if compliance is None:
        logger.warning("[Router] compliance is None → error branch.")
        return "error"

    if compliance.approved:
        logger.info("[Router] Design approved ✓ → commercial optimiser.")
        return "approved"

    # Design was rejected — decide whether to iterate
    if iteration >= max_iter:
        logger.warning(
            "[Router] Max iterations (%d) reached. Cannot satisfy constraints.",
            max_iter,
        )
        return "iteration_limit"

    logger.info(
        "[Router] Design rejected (iteration %d/%d) → redesign loop.",
        iteration + 1,
        max_iter,
    )
    return "redesign"


# ─────────────────────────────────────────────────────────────────────────────
# Increment iteration counter (called at the start of each redesign pass)
# ─────────────────────────────────────────────────────────────────────────────


def increment_iteration_node(state: AgentState) -> dict:
    """
    Lightweight node that bumps the ``iteration_count`` before re-entering
    the design loop.  Also logs and PRESERVES the redesign directives from
    the previous compliance check so Agent 2 can use them to adjust geometry.
    """
    count = state.get("iteration_count", 0) + 1
    compliance = state.get("compliance")

    # ── Extract directives from compliance BEFORE it gets cleared ──────
    directives: list[str] = []
    if compliance is not None:
        if isinstance(compliance, dict):
            directives = compliance.get("redesign_directives", [])
        elif hasattr(compliance, "redesign_directives"):
            directives = list(compliance.redesign_directives)

    if directives:
        logger.info(
            "[Orchestrator] Redesign directives for iteration %d:\n%s",
            count,
            "\n".join(f"  → {d}" for d in directives),
        )

    return {
        "iteration_count": count,
        "current_step": f"redesign_iteration_{count}",
        # Clear old geometry/compliance so agents recompute from scratch,
        # but PRESERVE redesign_directives for Agent 2 and
        # min_yield_strength_mpa for Agent 3.
        "geometry": None,
        "compliance": None,
        "redesign_directives": directives,
        # Preserve material constraint from previous iteration
        "min_yield_strength_mpa": state.get("min_yield_strength_mpa"),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Terminal node: assemble clarification response
# ─────────────────────────────────────────────────────────────────────────────


def clarification_node(state: AgentState) -> dict:
    """
    Terminal node reached when Agent 1 cannot extract complete requirements.
    Packages the clarification questions into ``final_report`` so the API
    can return them to the frontend / user.
    """
    requirements = state.get("requirements")
    questions = (
        requirements.clarification_questions
        if requirements
        else ["Please provide more details about your spring requirements."]
    )

    return {
        "current_step": "awaiting_clarification",
        "final_report": {
            "status": "needs_clarification",
            "clarification_questions": questions,
            "partial_requirements": (
                requirements.model_dump() if requirements else {}
            ),
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# Terminal node: iteration limit exceeded
# ─────────────────────────────────────────────────────────────────────────────


def iteration_limit_node(state: AgentState) -> dict:
    """
    Terminal node reached when the redesign loop hits ``max_iterations``.
    Returns the last computed geometry and compliance data even though the
    design was not fully approved, so the user can inspect what was attempted.
    """
    geometry = state.get("geometry")
    compliance = state.get("compliance")
    logger.error("[Orchestrator] Iteration limit reached. Surfacing last state.")

    return {
        "current_step": "iteration_limit_reached",
        "final_report": {
            "status": "iteration_limit_reached",
            "message": (
                f"The system could not produce a compliant design within "
                f"{state.get('max_iterations', 5)} iterations. "
                "The last attempted design is included below for reference."
            ),
            "last_geometry": geometry.model_dump() if geometry and hasattr(geometry, "model_dump") else {},
            "last_compliance": compliance.model_dump() if compliance and hasattr(compliance, "model_dump") else {},
            "errors": state.get("errors", []),
        },
    }
