"""
app/agents/agent5_commercial.py
─────────────────────────────────────────────────────────────────────────────
Agent 5 – Commercial Optimiser

Responsibilities
────────────────
* Build the list of viable spring proposals by combining geometry + material
  data. The primary proposal (P001) is the design approved by Agent 4; extra
  proposals come from the material candidates short-listed by Agent 3, each
  re-optimised and compliance-checked with pure tool calls (no LLM).
* Invoke ``commercial_scoring_tool`` to compute the weighted commercial index
  across ALL viable proposals and rank them.
* Assemble the final ``final_report`` dict that the FastAPI endpoint returns,
  structured to be immediately consumable by React/Recharts and Three.js.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from langchain_core.messages import AIMessage

from app.schemas.state import (
    AgentState,
    CommercialScore,
    ComplianceReport,
    MaterialProperties,
    SpringGeometry,
    UserRequirements,
)
from app.tools.commercial import commercial_scoring_tool
from app.tools.compliance import compliance_verification_tool
from app.tools.geometry import calculate_spring_geometry_tool
from app.tools.physics import FATIGUE_MIN_LOAD_RATIO

logger = logging.getLogger(__name__)

# Maximum number of alternative materials evaluated per run (cost control).
# NOTE: The effective cap is coupled to agent3_materials._MAX_CANDIDATES (also 3).
# Agent 3 surfaces at most _MAX_CANDIDATES total (selected + alternatives), so
# the alternatives slice passed here is at most _MAX_CANDIDATES - 1 = 2 in
# practice. Keep both constants in sync when changing either.
MAX_ALTERNATIVES = 3


def commercial_optimiser_node(state: AgentState) -> dict:
    """LangGraph node for Agent 5 – Commercial Optimiser."""
    logger.info("[Agent 5] Commercial Optimiser started.")

    geometry = state.get("geometry")
    material = state.get("material")
    requirements = state.get("requirements")
    compliance = state.get("compliance")

    if geometry is None or material is None:
        return _error(state, "MissingDependencies", "geometry or material is None")

    # ── Build proposals list ───────────────────────────────────────────────
    # P001 = the primary design already approved through the redesign loop.
    proposals = [_build_proposal("P001", geometry, material, compliance, requirements)]
    proposal_meta: dict[str, dict] = {
        "P001": {"geometry": geometry, "material": material, "compliance": compliance},
    }

    # ── Evaluate alternative material candidates (tool calls only) ────────
    candidates = state.get("material_candidates") or []
    alternatives = [
        c for c in candidates if c.material_id != material.material_id
    ][:MAX_ALTERNATIVES]

    for candidate in alternatives:
        evaluation = _evaluate_alternative(candidate, requirements)
        if evaluation is None:
            continue  # non-viable or failed evaluation → skip silently
        alt_geometry, alt_compliance = evaluation
        proposal_id = f"P{len(proposals) + 1:03d}"
        proposals.append(
            _build_proposal(
                proposal_id, alt_geometry, candidate, alt_compliance, requirements
            )
        )
        proposal_meta[proposal_id] = {
            "geometry": alt_geometry,
            "material": candidate,
            "compliance": alt_compliance,
        }
        logger.info(
            "[Agent 5] Viable alternative %s: %s", proposal_id, candidate.name
        )

    proposals_json = json.dumps(proposals)

    try:
        result_json = commercial_scoring_tool.invoke({"proposals": proposals_json})
        result = json.loads(result_json)
    except Exception as exc:
        return _error(state, type(exc).__name__, str(exc))

    if result.get("status") != "ok":
        return _error(state, "ToolError", result.get("message", "Unknown"))

    ranked = result["ranked_proposals"]
    chart_data = result["chart_data"]

    # Convert to CommercialScore objects
    scores = [
        CommercialScore(
            proposal_id=r["proposal_id"],
            wire_mass_kg=r["wire_mass_kg"],
            material_cost_usd=r["material_cost_usd"],
            estimated_life_cycles=r["estimated_life_cycles"],
            composite_score=r["composite_score"],
            rank=r["rank"],
        )
        for r in ranked
    ]

    # ── Commercial options (one entry per viable material) ────────────────
    # The recommended option is always the primary (P001) — the one that went
    # through the full redesign/compliance loop — but rank stays honest.
    options = [
        _build_option(r, proposal_meta[r["proposal_id"]])
        for r in ranked
        if r["proposal_id"] in proposal_meta
    ]

    # Guard: if the scoring tool ranked P001 outside its output (e.g. filtered)
    # ensure at least one option carries is_recommended=True.
    if options and not any(o["is_recommended"] for o in options):
        if "P001" in proposal_meta:
            logger.warning(
                "[Agent 5] No option has is_recommended=True; "
                "P001 missing from ranked output — marking first option as recommended."
            )
            options[0]["is_recommended"] = True
        else:
            logger.warning(
                "[Agent 5] No option has is_recommended=True and P001 is absent."
            )

    # ── Assemble final report ──────────────────────────────────────────────
    final_report = {
        "status": "approved",
        "summary": {
            "spring_type": requirements.spring_type if requirements else "unknown",
            "material": material.name,
            "applicable_standard": compliance.applicable_standard if compliance else "N/A",
            "approved": compliance.approved if compliance else False,
        },
        "geometry": geometry.model_dump() if hasattr(geometry, "model_dump") else {},
        "material": material.model_dump() if hasattr(material, "model_dump") else {},
        "compliance": compliance.model_dump() if compliance and hasattr(compliance, "model_dump") else {},
        "commercial": {
            "ranked_proposals": ranked,
            "chart_data": chart_data,
            "options": options,
        },
        "three_js_scene": {
            "spring": ranked[0]["three_js_params"] if ranked else {},
            "material_color": _material_color(material.name),
            "background": "#0d1117",
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    top = ranked[0] if ranked else {}
    logger.info(
        "[Agent 5] Best proposal: score=%.3f, cost=$%.4f, life=%d cycles (%d option(s))",
        top.get("composite_score", 0),
        top.get("material_cost_usd", 0),
        top.get("estimated_life_cycles", 0),
        len(options),
    )

    return {
        "commercial_proposals": scores,
        "final_report": final_report,
        "current_step": "commercial_optimiser",
        "messages": [AIMessage(content=(
            f"**Commercial Analysis Complete**\n"
            f"Viable material options: {len(options)}\n"
            f"Best proposal score: {top.get('composite_score', 0):.3f}\n"
            f"Material cost per spring: ${top.get('material_cost_usd', 0):.4f}\n"
            f"Estimated service life: {top.get('estimated_life_cycles', 0):,} cycles"
        ))],
    }


def _build_proposal(
    proposal_id: str,
    geometry: SpringGeometry,
    material: MaterialProperties,
    compliance: ComplianceReport | None,
    requirements: UserRequirements | None,
) -> dict:
    """Build the proposal dict consumed by ``commercial_scoring_tool``."""
    return {
        "proposal_id": proposal_id,
        "wire_diameter_mm": geometry.wire_diameter_mm,
        "mean_coil_diameter_mm": geometry.mean_coil_diameter_mm,
        "outer_diameter_mm": geometry.outer_diameter_mm,
        "active_coils": geometry.active_coils,
        "total_coils": geometry.total_coils,
        "free_length_mm": geometry.free_length_mm,
        "spring_rate_n_mm": geometry.spring_rate_n_mm,
        # Material properties needed for mass/cost calc
        "density_kg_m3": material.density_kg_m3,
        "cost_usd_per_kg": material.cost_usd_per_kg,
        "yield_strength_mpa": material.yield_strength_mpa,
        # Compliance scores
        "safety_factor_shear": compliance.safety_factor_shear if compliance else 1.0,
        "safety_factor_buckling": compliance.safety_factor_buckling if compliance else 1.0,
        # Application requirements
        "cycles_expected": requirements.cycles_expected if requirements else None,
        "cyclic_load": requirements.cyclic_load if requirements else False,
    }


def _evaluate_alternative(
    candidate: MaterialProperties,
    requirements: UserRequirements | None,
) -> tuple[SpringGeometry, ComplianceReport] | None:
    """Optimise geometry for a candidate material and verify compliance.

    Deterministic tool calls only (no LLM). Returns (geometry, compliance)
    when the candidate's optimised design passes compliance; None when the
    candidate is not viable or any step fails — a failing alternative must
    never crash the final report.
    """
    try:
        if requirements is None or not requirements.load_force_n:
            return None
        load = requirements.load_force_n
        deflection = requirements.deflection_mm
        if not deflection:
            if not requirements.spring_rate_n_mm:
                return None
            deflection = load / requirements.spring_rate_n_mm

        # ── Geometry optimisation with the candidate's properties ─────────
        geo_result = json.loads(calculate_spring_geometry_tool.invoke({
            "spring_type": requirements.spring_type,
            "load_force_n": load,
            "deflection_mm": deflection,
            "max_outer_diameter_mm": requirements.max_outer_diameter_mm,
            "max_free_length_mm": requirements.max_free_length_mm,
            "shear_modulus_gpa": candidate.shear_modulus_gpa,
            "yield_strength_mpa": candidate.yield_strength_mpa,
            "cyclic_load": requirements.cyclic_load or False,
        }))
        if geo_result.get("status") != "ok":
            logger.info(
                "[Agent 5] Alternative '%s' has no feasible geometry: %s",
                candidate.name, geo_result.get("message", "unknown"),
            )
            return None

        geom_data = geo_result["geometry"]
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

        # ── Compliance verification (same inputs Agent 4 would use) ───────
        tool_input: dict[str, object] = {
            "wire_diameter_mm": geometry.wire_diameter_mm,
            "mean_coil_diameter_mm": geometry.mean_coil_diameter_mm,
            "active_coils": geometry.active_coils,
            "free_length_mm": geometry.free_length_mm,
            "spring_rate_n_mm": geometry.spring_rate_n_mm,
            "load_force_n": load,
            "yield_strength_mpa": candidate.yield_strength_mpa,
            "shear_modulus_gpa": candidate.shear_modulus_gpa,
            "spring_type": requirements.spring_type,
            "max_free_length_mm": requirements.max_free_length_mm,
            "cyclic_load": requirements.cyclic_load or False,
        }
        if requirements.cyclic_load:
            tool_input["min_force_n"] = FATIGUE_MIN_LOAD_RATIO * load
            tool_input["max_force_n"] = load

        comp_result = json.loads(compliance_verification_tool.invoke(tool_input))
        if comp_result.get("status") != "ok":
            return None

        report_data = comp_result["report"]
        compliance = ComplianceReport(
            approved=report_data["approved"],
            safety_factor_shear=report_data["safety_factor_shear"],
            safety_factor_buckling=report_data["safety_factor_buckling"],
            safety_factor_fatigue=report_data.get("safety_factor_fatigue"),
            applicable_standard=report_data["applicable_standard"],
            failure_modes=report_data.get("failure_modes", []),
            redesign_directives=report_data.get("redesign_directives", []),
        )
        if not compliance.approved:
            logger.info(
                "[Agent 5] Alternative '%s' rejected by compliance: %s",
                candidate.name, "; ".join(compliance.failure_modes),
            )
            return None

        return geometry, compliance

    except Exception as exc:
        logger.warning(
            "[Agent 5] Alternative '%s' evaluation failed (skipped): %s",
            candidate.name, exc,
        )
        return None


def _build_option(ranked_entry: dict, meta: dict) -> dict:
    """Assemble one final_report.commercial.options entry."""
    geometry = meta["geometry"]
    material = meta["material"]
    compliance = meta["compliance"]
    return {
        "proposal_id": ranked_entry["proposal_id"],
        "material": material.model_dump() if hasattr(material, "model_dump") else {},
        "geometry": geometry.model_dump() if hasattr(geometry, "model_dump") else {},
        "compliance": compliance.model_dump() if compliance and hasattr(compliance, "model_dump") else {},
        "wire_mass_kg": ranked_entry["wire_mass_kg"],
        "material_cost_usd": ranked_entry["material_cost_usd"],
        "estimated_life_cycles": ranked_entry["estimated_life_cycles"],
        "composite_score": ranked_entry["composite_score"],
        "rank": ranked_entry["rank"],
        "is_recommended": ranked_entry["proposal_id"] == "P001",
    }


def _material_color(material_name: str) -> str:
    """Map material names to Three.js-friendly hex color codes."""
    palette = {
        "Music Wire": "#c0c0c0",
        "Stainless": "#e8e8f0",
        "Phosphor Bronze": "#cd7f32",
        "Chrome-Silicon": "#708090",
        "Chrome-Vanadium": "#5c6bc0",
        "Inconel": "#ffca28",
    }
    for key, color in palette.items():
        if key.lower() in material_name.lower():
            return color
    return "#9e9e9e"   # default: steel grey


def _error(state: AgentState, error_type: str, message: str) -> dict:
    logger.error("[Agent 5] %s: %s", error_type, message)
    return {
        "current_step": "commercial_optimiser_failed",
        "errors": state.get("errors", []) + [{
            "step": "commercial_optimiser",
            "error_type": error_type,
            "message": message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }],
    }
