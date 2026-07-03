"""
app/agents/agent2_design.py
─────────────────────────────────────────────────────────────────────────────
Agent 2 – Design Engineer

Responsibilities
────────────────
* Read the structured ``UserRequirements`` from state.
* Decide which parametric inputs to pass to ``calculate_spring_geometry_tool``.
* Invoke the tool directly (no LLM needed for pure calculation; LLM is used
  to interpret partial inputs and decide on defaults for missing values).
* Parse the tool's JSON response back into the state's ``geometry`` field.

Design decision
───────────────
The tool call is made programmatically here (not via LLM ToolCall) because the
geometry calculation is deterministic.  The LLM is invoked only when the
requirements contain ambiguous or missing values that need intelligent defaults
(e.g. missing deflection inferred from spring rate + force).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.core.llm_factory import get_factory, rotate_llm_on_quota_error
from app.schemas.state import AgentState, SpringGeometry
from app.tools.spring_tools import calculate_spring_geometry_tool

logger = logging.getLogger(__name__)

_DEFAULTS_PROMPT = """You are a mechanical spring design engineer.
Given the following partial requirements JSON, provide a JSON object with the
MINIMUM information needed to call the geometry calculator:
- spring_type (str)
- load_force_n (float)
- deflection_mm (float)
- max_outer_diameter_mm (float or null)
- max_free_length_mm (float or null)
- shear_modulus_gpa (float) — use 79.3 if no material selected yet
- yield_strength_mpa (float) — use 1500 if no material selected yet

Apply engineering judgment for any null values. Return ONLY valid JSON."""

_REDESIGN_PROMPT = """You are a mechanical spring design engineer adjusting a design
that FAILED the normative compliance check.

Previous requirements:
{requirements_json}

Previous compliance failure:
{compliance_json}

Redesign directives:
{directives}

Your task: Adjust the tool parameters so the NEXT design iteration succeeds.
Return a JSON object with ONLY these fields (all required):
- spring_type (str)
- load_force_n (float)
- deflection_mm (float)
- max_outer_diameter_mm (float or null)
- max_free_length_mm (float or null)
- shear_modulus_gpa (float)
- yield_strength_mpa (float)

Strategy:
- If the issue is LOW SHEAR SAFETY FACTOR: REDUCE spring index C by using a LARGER
  wire diameter and/or a SMALLER mean coil diameter.
- If the issue is BUCKLING (high slenderness): REDUCE free length by using FEWER
  active coils (n_a) but compensate with thicker wire to maintain the spring rate.
- If free length exceeds constraint: REDUCE n_a and/or wire diameter, but be careful
  not to compromise shear safety.
- NEVER increase max_outer_diameter_mm or max_free_length_mm — those are hard limits.

Return ONLY valid JSON. No markdown, no explanation."""


def design_engineer_node(state: AgentState) -> dict:
    """LangGraph node for Agent 2 – Design Engineer."""
    logger.info("[Agent 2] Design Engineer started.")

    requirements = state.get("requirements")
    if requirements is None:
        return {
            "current_step": "design_engineer_failed",
            "errors": state.get("errors", []) + [{
                "step": "design_engineer",
                "error_type": "MissingRequirements",
                "message": "requirements field is None in state",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }],
        }

    # Use material shear modulus and yield if already selected
    material = state.get("material")
    G = material.shear_modulus_gpa if material else 79.3
    Sy = material.yield_strength_mpa if material else 1_500.0

    # ── Read redesign directives from state (preserved across iterations
    #    by increment_iteration_node — NOT from compliance, which is cleared).
    redesign_directives: list[str] = list(state.get("redesign_directives", []))
    if redesign_directives:
        logger.info(
            "[Agent 2] Redesign iteration — applying directives: %s",
            "; ".join(redesign_directives),
        )

    # ── If we have force + deflection, call tool directly ─────────────────
    if requirements.load_force_n and requirements.deflection_mm:
        tool_input = {
            "spring_type": requirements.spring_type,
            "load_force_n": requirements.load_force_n,
            "deflection_mm": requirements.deflection_mm,
            "max_outer_diameter_mm": requirements.max_outer_diameter_mm,
            "max_free_length_mm": requirements.max_free_length_mm,
            "shear_modulus_gpa": G,
            "yield_strength_mpa": Sy,
        }
        # If redesign directives exist, use LLM to adjust parameters
        if redesign_directives:
            return _adjust_and_design(
                tool_input, requirements, redesign_directives, G, Sy, state
            )
    elif requirements.spring_rate_n_mm and requirements.load_force_n:
        # Compute deflection from spring rate
        deflection = requirements.load_force_n / requirements.spring_rate_n_mm
        tool_input = {
            "spring_type": requirements.spring_type,
            "load_force_n": requirements.load_force_n,
            "deflection_mm": deflection,
            "max_outer_diameter_mm": requirements.max_outer_diameter_mm,
            "max_free_length_mm": requirements.max_free_length_mm,
            "shear_modulus_gpa": G,
            "yield_strength_mpa": Sy,
        }
        if redesign_directives:
            return _adjust_and_design(
                tool_input, requirements, redesign_directives, G, Sy, state
            )
    else:
        # ── Fallback: ask LLM to fill in reasonable defaults ────────────
        factory = get_factory()
        req_json = requirements.model_dump_json(indent=2)
        messages = [
            SystemMessage(content=_DEFAULTS_PROMPT),
            HumanMessage(content=req_json),
        ]
        try:
            llm = factory.get_llm()
            response = llm.invoke(messages)
            raw = response.content.strip().lstrip("```json").rstrip("```").strip()
            tool_input = json.loads(raw)
        except Exception as exc:
            try:
                rotate_llm_on_quota_error(exc)
            except RuntimeError:
                pass
            return {
                "current_step": "design_engineer_failed",
                "errors": state.get("errors", []) + [{
                    "step": "design_engineer",
                    "error_type": "LLMDefaultsFailed",
                    "message": str(exc),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }],
            }

    # ── Invoke the geometry tool ───────────────────────────────────────────
    try:
        result_json = calculate_spring_geometry_tool.invoke(tool_input)
        result = json.loads(result_json)

        if result.get("status") != "ok":
            raise ValueError(result.get("message", "Unknown tool error"))

        geom_data = result["geometry"]
        geometry = SpringGeometry(
            wire_diameter_mm=geom_data["wire_diameter_mm"],
            mean_coil_diameter_mm=geom_data["mean_coil_diameter_mm"],
            outer_diameter_mm=geom_data["outer_diameter_mm"],
            inner_diameter_mm=geom_data["inner_diameter_mm"],
            active_coils=geom_data["active_coils"],
            total_coils=geom_data["total_coils"],
            free_length_mm=geom_data["free_length_mm"],
            pitch_mm=geom_data["pitch_mm"],
            spring_index=geom_data["spring_index"],
            spring_rate_n_mm=geom_data["spring_rate_n_mm"],
            torsion_moment_n_mm=geom_data.get("torsion_moment_n_mm"),
            angular_deflection_deg=geom_data.get("angular_deflection_deg"),
        )
        logger.info(
            "[Agent 2] Geometry computed: d=%.3f mm, D=%.3f mm, n_a=%.1f",
            geometry.wire_diameter_mm,
            geometry.mean_coil_diameter_mm,
            geometry.active_coils,
        )
        return {
            "geometry": geometry,
            "current_step": "design_engineer",
            "messages": [AIMessage(content=f"Geometry computed: {geom_data}")],
        }

    except Exception as exc:
        logger.exception("[Agent 2] Tool invocation failed")
        return {
            "current_step": "design_engineer_failed",
            "errors": state.get("errors", []) + [{
                "step": "design_engineer",
                "error_type": type(exc).__name__,
                "message": str(exc),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }],
        }


def _adjust_and_design(
    base_tool_input: dict,
    requirements,
    directives: list[str],
    G: float,
    Sy: float,
    state: dict,
) -> dict:
    """Use LLM to adjust tool_input based on compliance redesign directives."""
    try:
        factory = get_factory()
        req_json = requirements.model_dump_json(indent=2)
        prompt = _REDESIGN_PROMPT.format(
            requirements_json=req_json,
            compliance_json=(
                state.get("compliance").model_dump_json(indent=2)
                if state.get("compliance")
                else "{}"
            ),
            directives="\n".join(f"- {d}" for d in directives),
        )
        messages = [
            SystemMessage(content=prompt),
        ]
        llm = factory.get_llm()
        response = llm.invoke(messages)
        raw = response.content.strip().lstrip("```json").rstrip("```").strip()
        adjusted = json.loads(raw)

        # Merge: keep original values unless LLM changed them
        tool_input = {**base_tool_input}
        for key in ("spring_type", "load_force_n", "deflection_mm",
                     "max_outer_diameter_mm", "max_free_length_mm",
                     "shear_modulus_gpa", "yield_strength_mpa"):
            if key in adjusted and adjusted[key] is not None:
                tool_input[key] = adjusted[key]

        logger.info("[Agent 2] Adjusted tool_input based on redesign directives.")

    except Exception as exc:
        logger.warning("[Agent 2] LLM redesign adjustment failed: %s. Using base input.", exc)
        tool_input = {**base_tool_input}

    # ── Fall through: call tool with (possibly adjusted) input ──────────
    try:
        result_json = calculate_spring_geometry_tool.invoke(tool_input)
        result = json.loads(result_json)
        if result.get("status") != "ok":
            raise ValueError(result.get("message", "Unknown tool error"))
        geom_data = result["geometry"]
        geometry = SpringGeometry(
            wire_diameter_mm=geom_data["wire_diameter_mm"],
            mean_coil_diameter_mm=geom_data["mean_coil_diameter_mm"],
            outer_diameter_mm=geom_data["outer_diameter_mm"],
            inner_diameter_mm=geom_data["inner_diameter_mm"],
            active_coils=geom_data["active_coils"],
            total_coils=geom_data["total_coils"],
            free_length_mm=geom_data["free_length_mm"],
            pitch_mm=geom_data["pitch_mm"],
            spring_index=geom_data["spring_index"],
            spring_rate_n_mm=geom_data["spring_rate_n_mm"],
            torsion_moment_n_mm=geom_data.get("torsion_moment_n_mm"),
            angular_deflection_deg=geom_data.get("angular_deflection_deg"),
        )
        logger.info(
            "[Agent 2] Redesign geometry: d=%.3f mm, D=%.3f mm, n_a=%.1f",
            geometry.wire_diameter_mm,
            geometry.mean_coil_diameter_mm,
            geometry.active_coils,
        )
        return {
            "geometry": geometry,
            "current_step": "design_engineer",
            "messages": [AIMessage(content=f"Redesign geometry: {geom_data}")],
        }
    except Exception as exc:
        logger.exception("[Agent 2] Redesign tool invocation failed")
        return {
            "current_step": "design_engineer_failed",
            "errors": state.get("errors", []) + [{
                "step": "design_engineer",
                "error_type": type(exc).__name__,
                "message": str(exc),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }],
        }
