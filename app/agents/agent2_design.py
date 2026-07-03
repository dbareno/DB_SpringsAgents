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
            "cyclic_load": requirements.cyclic_load or False,
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
            "cyclic_load": requirements.cyclic_load or False,
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
    """Usa redesign_advisor_tool para ajustes NUMÉRICOS exactos.

    En vez de preguntarle al LLM cómo ajustar (que adivina), llamamos
    al advisor tool que computa derivadas analíticas y devuelve Δ% exactos.
    """
    from app.tools.spring_tools import redesign_advisor_tool

    compliance = state.get("compliance")
    geometry = state.get("geometry")

    try:
        # ── Llamar al advisor tool ─────────────────────────────────────
        advisor_result = json.loads(redesign_advisor_tool.invoke({
            "wire_diameter_mm": geometry.wire_diameter_mm if geometry else 1.0,
            "mean_coil_diameter_mm": geometry.mean_coil_diameter_mm if geometry else 10.0,
            "active_coils": geometry.active_coils if geometry else 5.0,
            "free_length_mm": geometry.free_length_mm if geometry else 50.0,
            "load_force_n": base_tool_input.get("load_force_n", 100.0),
            "deflection_mm": base_tool_input.get("deflection_mm", 20.0),
            "yield_strength_mpa": Sy,
            "max_outer_diameter_mm": base_tool_input.get("max_outer_diameter_mm"),
            "max_free_length_mm": base_tool_input.get("max_free_length_mm"),
            "safety_factor_shear": compliance.safety_factor_shear if compliance else None,
            "safety_factor_buckling": compliance.safety_factor_buckling if compliance else None,
            "safety_factor_fatigue": compliance.safety_factor_fatigue if compliance else None,
            "slenderness_ratio": geometry.free_length_mm / max(geometry.mean_coil_diameter_mm, 0.001)
                                  if geometry else None,
            "failure_modes": json.dumps(compliance.failure_modes if compliance else []),
        }))

        if advisor_result.get("status") != "ok":
            raise ValueError(advisor_result.get("message", "Advisor tool failed"))

        advisor = advisor_result["advisor"]
        adjustments: dict = advisor.get("adjustments", {})
        material_constraints: dict = advisor.get("material_constraints", {})
        action: str = advisor.get("action", "relax-constraints-or-stop")
        suggestions: list[str] = advisor.get("suggestions", [])

        logger.info(
            "[Agent 2] Redesign advisor: action=%s, adjustments=%s, material=%s",
            action, adjustments, material_constraints,
        )

        # ── Aplicar ajustes al tool_input ──────────────────────────────
        tool_input = {**base_tool_input}

        # Mapeo de campos: clave en adjustments → clave en tool_input
        field_map = {
            "wire_diameter_mm": None,  # no se pasa directo al tool
            "mean_coil_diameter_mm": None,
            "free_length_mm": None,
        }

        # Los ajustes porcentuales se traducen a constraints del tool
        # (el optimizer recibe OD y FL como constraints, no d o D directos)
        if "mean_coil_diameter_mm" in adjustments:
            delta = adjustments["mean_coil_diameter_mm"]
            if delta > 0 and tool_input.get("max_outer_diameter_mm"):
                # Aumentar D → necesitamos más OD
                current_od = tool_input["max_outer_diameter_mm"]
                tool_input["max_outer_diameter_mm"] = round(
                    current_od * (1.0 + delta / 100.0), 2
                )
                logger.info("[Agent 2] Relajando OD constraint en +%.1f%%", delta)
            elif delta < 0 and tool_input.get("max_outer_diameter_mm"):
                # Reducir D → OD más pequeño (más restrictivo)
                current_od = tool_input["max_outer_diameter_mm"]
                tool_input["max_outer_diameter_mm"] = round(
                    current_od * (1.0 + delta / 100.0), 2
                )

        if "free_length_mm" in adjustments:
            delta = adjustments["free_length_mm"]
            if delta < 0 and tool_input.get("max_free_length_mm"):
                current_fl = tool_input["max_free_length_mm"]
                tool_input["max_free_length_mm"] = round(
                    current_fl * (1.0 + delta / 100.0), 2
                )
                logger.info("[Agent 2] Ajustando FL constraint en %.1f%%", delta)

        if "yield_strength_mpa" in material_constraints:
            # Si el advisor sugiere un material más fuerte, actualizamos Sy
            tool_input["yield_strength_mpa"] = float(material_constraints["min_yield_strength_mpa"])
            logger.info(
                "[Agent 2] Actualizando Sy a %.0f MPa por recomendación del advisor.",
                tool_input["yield_strength_mpa"],
            )
            # También subimos G ligeramente (aceros más duros tienen G similar)
            tool_input["shear_modulus_gpa"] = max(G, 79.3)

    except Exception as exc:
        logger.warning("[Agent 2] Advisor tool failed: %s. Fallback to LLM.", exc)
        # Fallback: usar LLM como antes
        try:
            factory = get_factory()
            req_json = requirements.model_dump_json(indent=2) if hasattr(requirements, 'model_dump_json') else str(requirements)
            prompt = _REDESIGN_PROMPT.format(
                requirements_json=req_json,
                compliance_json=(
                    compliance.model_dump_json(indent=2)
                    if compliance else "{}"
                ),
                directives="\n".join(f"- {d}" for d in directives),
            )
            messages = [SystemMessage(content=prompt)]
            llm = factory.get_llm()
            response = llm.invoke(messages)
            raw = response.content.strip().lstrip("```json").rstrip("```").strip()
            adjusted = json.loads(raw)
            tool_input = {**base_tool_input}
            for key in ("spring_type", "load_force_n", "deflection_mm",
                         "max_outer_diameter_mm", "max_free_length_mm",
                         "shear_modulus_gpa", "yield_strength_mpa"):
                if key in adjusted and adjusted[key] is not None:
                    tool_input[key] = adjusted[key]
        except Exception:
            tool_input = {**base_tool_input}
            logger.warning("[Agent 2] LLM fallback also failed — using base input.")

    # ── Invocar el geometry tool ───────────────────────────────────────────
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

        # Si el advisor pidió material más fuerte, guardar en state
        updates: dict = {
            "geometry": geometry,
            "current_step": "design_engineer",
            "messages": [AIMessage(content=f"Redesign geometry: {geom_data}")],
        }
        if material_constraints and "min_yield_strength_mpa" in material_constraints:
            updates["min_yield_strength_mpa"] = float(material_constraints["min_yield_strength_mpa"])
            logger.info(
                "[Agent 2] Guardando min_yield_strength_mpa=%.0f para Agent 3.",
                updates["min_yield_strength_mpa"],
            )

        return updates

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
