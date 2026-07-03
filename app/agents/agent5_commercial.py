"""
app/agents/agent5_commercial.py
─────────────────────────────────────────────────────────────────────────────
Agent 5 – Commercial Optimiser

Responsibilities
────────────────
* Build the list of viable spring proposals by combining geometry + material
  data (a single proposal in a typical run; multiple if the graph explored
  alternatives via iteration).
* Invoke ``commercial_scoring_tool`` to compute the weighted commercial index.
* Assemble the final ``final_report`` dict that the FastAPI endpoint returns,
  structured to be immediately consumable by React/Recharts and Three.js.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from langchain_core.messages import AIMessage

from app.schemas.state import AgentState, CommercialScore
from app.tools.spring_tools import commercial_scoring_tool

logger = logging.getLogger(__name__)


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
    proposal = {
        "proposal_id": "P001",
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
    proposals_json = json.dumps([proposal])

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

    # ── Assemble final report ──────────────────────────────────────────────
    final_report = {
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
        "[Agent 5] Best proposal: score=%.3f, cost=$%.4f, life=%d cycles",
        top.get("composite_score", 0),
        top.get("material_cost_usd", 0),
        top.get("estimated_life_cycles", 0),
    )

    return {
        "commercial_proposals": scores,
        "final_report": final_report,
        "current_step": "commercial_optimiser",
        "messages": [AIMessage(content=(
            f"**Commercial Analysis Complete**\n"
            f"Best proposal score: {top.get('composite_score', 0):.3f}\n"
            f"Material cost per spring: ${top.get('material_cost_usd', 0):.4f}\n"
            f"Estimated service life: {top.get('estimated_life_cycles', 0):,} cycles"
        ))],
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
