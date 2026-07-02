"""
app/agents/agent4_compliance.py
─────────────────────────────────────────────────────────────────────────────
Agent 4 – Normative Inspector

Responsibilities
────────────────
* Run the ``compliance_verification_tool`` against the current geometry +
  material combination.
* If APPROVED → advance graph toward Agent 5.
* If REJECTED → translate the raw failure modes into actionable redesign
  directives and set ``current_step = "redesign_needed"`` so the Orchestrator
  (Agent 6) loops back to Agent 2.
* Uses the LLM to produce a human-friendly compliance summary paragraph.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from langchain_core.messages import AIMessage

from app.core.llm_factory import get_factory, rotate_llm_on_quota_error
from app.schemas.state import AgentState, ComplianceReport
from app.tools.spring_tools import compliance_verification_tool

logger = logging.getLogger(__name__)


def normative_inspector_node(state: AgentState) -> dict:
    """LangGraph node for Agent 4 – Normative Inspector."""
    logger.info("[Agent 4] Normative Inspector started.")

    geometry = state.get("geometry")
    material = state.get("material")
    requirements = state.get("requirements")

    if geometry is None or material is None:
        return _error(state, "MissingDependencies", "geometry or material is None")

    tool_input = {
        "wire_diameter_mm": geometry.wire_diameter_mm,
        "mean_coil_diameter_mm": geometry.mean_coil_diameter_mm,
        "active_coils": geometry.active_coils,
        "free_length_mm": geometry.free_length_mm,
        "spring_rate_n_mm": geometry.spring_rate_n_mm,
        "load_force_n": requirements.load_force_n if requirements else 0.0,
        "yield_strength_mpa": material.yield_strength_mpa,
        "shear_modulus_gpa": material.shear_modulus_gpa,
        "spring_type": requirements.spring_type if requirements else "compression",
        "cyclic_load": requirements.cyclic_load if requirements else False,
    }

    # Add fatigue inputs if available
    if requirements and requirements.cyclic_load and requirements.load_force_n:
        F = requirements.load_force_n
        tool_input["min_force_n"] = 0.1 * F      # assume 10% min load
        tool_input["max_force_n"] = F

    try:
        result_json = compliance_verification_tool.invoke(tool_input)
        result = json.loads(result_json)
    except Exception as exc:
        return _error(state, type(exc).__name__, str(exc))

    if result.get("status") != "ok":
        return _error(state, "ToolError", result.get("message", "Unknown"))

    report_data = result["report"]
    compliance = ComplianceReport(
        approved=report_data["approved"],
        safety_factor_shear=report_data["safety_factor_shear"],
        safety_factor_buckling=report_data["safety_factor_buckling"],
        safety_factor_fatigue=report_data.get("safety_factor_fatigue"),
        applicable_standard=report_data["applicable_standard"],
        failure_modes=report_data.get("failure_modes", []),
        redesign_directives=report_data.get("redesign_directives", []),
    )

    # ── Generate LLM narrative summary ────────────────────────────────────
    narrative = _generate_narrative(compliance)

    status_label = "APPROVED ✓" if compliance.approved else "REJECTED ✗"
    logger.info(
        "[Agent 4] Compliance result: %s | Sf_shear=%.3f | Sf_buckling=%.3f",
        status_label,
        compliance.safety_factor_shear,
        compliance.safety_factor_buckling,
    )

    next_step = "normative_approved" if compliance.approved else "redesign_needed"
    return {
        "compliance": compliance,
        "current_step": next_step,
        "messages": [AIMessage(content=narrative)],
    }


def _generate_narrative(compliance: ComplianceReport) -> str:
    """Build a concise human-readable compliance summary."""
    lines = [
        f"**Compliance Check ({compliance.applicable_standard})**",
        f"Result: {'✅ APPROVED' if compliance.approved else '❌ REJECTED'}",
        f"• Shear safety factor:   {compliance.safety_factor_shear:.3f}",
        f"• Buckling safety factor:{compliance.safety_factor_buckling:.3f}",
    ]
    if compliance.safety_factor_fatigue is not None:
        lines.append(f"• Fatigue safety factor: {compliance.safety_factor_fatigue:.3f}")
    if compliance.failure_modes:
        lines.append("\n**Failure modes detected:**")
        lines.extend(f"  – {fm}" for fm in compliance.failure_modes)
    if compliance.redesign_directives:
        lines.append("\n**Redesign directives for next iteration:**")
        lines.extend(f"  → {rd}" for rd in compliance.redesign_directives)
    return "\n".join(lines)


def _error(state: AgentState, error_type: str, message: str) -> dict:
    logger.error("[Agent 4] %s: %s", error_type, message)
    return {
        "current_step": "normative_inspector_failed",
        "errors": state.get("errors", []) + [{
            "step": "normative_inspector",
            "error_type": error_type,
            "message": message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }],
    }
