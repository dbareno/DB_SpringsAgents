"""
app/agents/agent3_materials.py
─────────────────────────────────────────────────────────────────────────────
Agent 3 – Materials Engineer

Responsibilities
────────────────
* Query the materials catalogue (SQL stub via ``query_material_properties_tool``).
* Evaluate the returned candidates against the application's environmental
  and operational constraints.
* Select the best material and write it to ``state["material"]``.
* If no material matches, inject an error and trigger redesign.

The LLM is used here to reason about material trade-offs (cost vs. strength vs.
corrosion vs. temperature) and produce a human-readable justification stored in
the message history.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.core.llm_factory import get_factory, rotate_llm_on_quota_error
from app.schemas.state import AgentState, MaterialProperties
from app.tools.spring_tools import query_material_properties_tool

logger = logging.getLogger(__name__)

_SELECTION_PROMPT = """You are a mechanical materials engineer.
Given the list of candidate spring materials (JSON) and the application
requirements (JSON), select the SINGLE best material and justify why.

Return ONLY a JSON object:
{
  "selected_material_id": <int>,
  "justification": "<1-3 sentence rationale>"
}"""


def materials_engineer_node(state: AgentState) -> dict:
    """LangGraph node for Agent 3 – Materials Engineer."""
    logger.info("[Agent 3] Materials Engineer started.")

    requirements = state.get("requirements")
    if requirements is None:
        return _error(state, "MissingRequirements", "requirements is None")

    # ── Call material query tool ───────────────────────────────────────────
    tool_input = {
        "operating_temperature_c": requirements.operating_temperature_c or 25.0,
        "corrosion_resistant": requirements.corrosion_resistant,
        "cyclic_load": requirements.cyclic_load,
        "spring_type": requirements.spring_type,
    }
    try:
        result_json = query_material_properties_tool.invoke(tool_input)
        result = json.loads(result_json)
    except Exception as exc:
        return _error(state, type(exc).__name__, str(exc))

    if result.get("status") == "no_match":
        return _error(state, "NoMaterialMatch", result.get("message", ""))

    if result.get("status") != "ok":
        return _error(state, "ToolError", result.get("message", "Unknown"))

    candidates: list[dict] = result["candidates"]

    # ── Fast-path: if only one candidate, skip LLM ────────────────────────
    if len(candidates) == 1:
        selected = candidates[0]
        justification = f"Only one material satisfies all constraints: {selected['name']}."
    else:
        # ── Ask LLM to reason about the trade-offs ─────────────────────
        factory = get_factory()
        messages = [
            SystemMessage(content=_SELECTION_PROMPT),
            HumanMessage(content=json.dumps({
                "candidates": candidates,
                "requirements": requirements.model_dump(),
            }, indent=2)),
        ]
        for _ in range(len(factory._settings.llm_priority_order)):
            try:
                llm = factory.get_llm()
                response = llm.invoke(messages)
                raw = response.content.strip().lstrip("```json").rstrip("```").strip()
                selection = json.loads(raw)
                break
            except Exception as exc:
                try:
                    rotate_llm_on_quota_error(exc)
                except RuntimeError as all_done:
                    return _error(state, "AllProvidersExhausted", str(all_done))
        else:
            # Fallback: take the first (top-scored) candidate
            selection = {"selected_material_id": candidates[0]["material_id"]}
            justification = "Selected automatically (LLM unavailable)."

        sel_id = selection.get("selected_material_id")
        justification = selection.get("justification", "")
        matching = [c for c in candidates if c["material_id"] == sel_id]
        selected = matching[0] if matching else candidates[0]

    material = MaterialProperties(**selected)
    logger.info(
        "[Agent 3] Selected material: %s (G=%.1f GPa, Sy=%.0f MPa, $%.2f/kg)",
        material.name,
        material.shear_modulus_gpa,
        material.yield_strength_mpa,
        material.cost_usd_per_kg,
    )
    return {
        "material": material,
        "current_step": "materials_engineer",
        "messages": [AIMessage(content=(
            f"Selected material: **{material.name}**\n"
            f"G = {material.shear_modulus_gpa} GPa | "
            f"Sy = {material.yield_strength_mpa} MPa | "
            f"Cost = ${material.cost_usd_per_kg}/kg\n"
            f"Justification: {justification}"
        ))],
    }


def _error(state: AgentState, error_type: str, message: str) -> dict:
    logger.error("[Agent 3] %s: %s", error_type, message)
    return {
        "current_step": "materials_engineer_failed",
        "errors": state.get("errors", []) + [{
            "step": "materials_engineer",
            "error_type": error_type,
            "message": message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }],
    }
