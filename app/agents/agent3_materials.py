"""
app/agents/agent3_materials.py
─────────────────────────────────────────────────────────────────────────────
Agent 3 – Materials Engineer  (v2 — intelligent selection)

Responsibilities
────────────────
* Parse the user's raw input for material preferences / hints.
* Query the materials catalogue with full context (temperature, corrosion,
  cyclic load, cost, user preference).
* Score and rank candidates using multi-factor suitability (strength/cost,
  temperature margin, fatigue bonus, preference boost).
* Use the LLM (or deterministic fallback) to produce an engineering-grade
  justification that explains trade-offs between candidates and why the
  selected material is best for THIS application.
* If no material satisfies all hard constraints, show the closest alternatives
  and explain what to relax.
* If the user asked for an unsuitable material, explain WHY it doesn't work
  and what to use instead.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.core.llm_factory import get_factory, rotate_llm_on_quota_error
from app.schemas.state import AgentState, MaterialProperties
from app.tools.spring_tools import query_material_properties_tool

logger = logging.getLogger(__name__)

# ── Keywords that hint at material preferences in raw_input ────────────────
_MATERIAL_KEYWORDS: dict[str, str] = {
    # material names → canonical name fragment for matching
    "music wire": "music wire",
    "a228": "music wire",
    "piano wire": "music wire",
    "hard drawn": "hard-drawn",
    "a227": "hard-drawn",
    "stainless": "stainless steel",
    "a313": "stainless steel",
    "302": "stainless steel",
    "phosphor bronze": "phosphor bronze",
    "b197": "phosphor bronze",
    "bronze": "phosphor bronze",
    "chrome silicon": "chrome-silicon",
    "a401": "chrome-silicon",
    "chrome vanadium": "chrome-vanadium",
    "chrome-vanadium": "chrome-vanadium",
    "inconel": "inconel",
    "718": "inconel",
    # user goals / property hints
    "cheap": "__cheap__",
    "economical": "__cheap__",
    "budget": "__cheap__",
    "strong": "__strong__",
    "high strength": "__strong__",
    "light": "__light__",
    "lightweight": "__light__",
    "corrosion resistant": "__corrosion__",
    "rust proof": "__corrosion__",
    "non corrosive": "__corrosion__",
    "high temp": "__hightemp__",
    "high temperature": "__hightemp__",
}

_GOAL_BONUS: dict[str, str] = {
    "__cheap__": "The user prioritises low cost.",
    "__strong__": "The user needs high strength.",
    "__light__": "The user wants a lightweight material.",
    "__corrosion__": "The user explicitly wants corrosion resistance.",
    "__hightemp__": "The user needs high-temperature capability.",
}

# ── LLM prompt template ────────────────────────────────────────────────────
_SELECTION_PROMPT = """You are a SENIOR mechanical materials engineer selecting spring wire.

## Context
- **User preference/goal**: {user_goal}
- **Preferred material mentioned**: {user_material}
- **Spring type**: {spring_type}

## Candidates (ranked by multi-factor score)
{candidates_table}

## Requirements
- Temperature: {temp}°C
- Corrosion resistant: {corrosion}
- Cyclic/fatigue load: {cyclic}
- Cost ceiling: {cost_str}

## Your task
Select the SINGLE best material for THIS application. Consider:
1. Yield strength vs. cost ratio (base competitiveness)
2. Temperature margin (how much headroom above operating temp)
3. Fatigue suitability (cyclic loads favour high-Sy materials)
4. User preference (respect it, but explain if it's suboptimal)
5. Corrosion requirements

Return ONLY a JSON object:
```json
{{
  "selected_material_id": <int>,
  "justification": "<2-4 sentence engineering justification>",
  "runner_up_name": "<name of second-best option or null>",
  "runner_up_reason": "<why runner-up wasn't chosen or null>",
  "candidate_summary": "<1 sentence summarising the field>"
}}```"""


# ── Helpers ─────────────────────────────────────────────────────────────────


def _extract_user_material_preference(raw_input: str) -> tuple[str | None, str]:
    """Scan raw_input for known material keywords.

    Returns (canonical_name_or_None, goal_description).
    """
    lowered = raw_input.lower()
    matched_name: str | None = None
    goals: list[str] = []

    for keyword, canonical in _MATERIAL_KEYWORDS.items():
        if keyword in lowered:
            if canonical.startswith("__"):
                goals.append(_GOAL_BONUS.get(canonical, canonical))
            else:
                matched_name = canonical

    goal_text = " ".join(goals) if goals else "No specific goal detected."
    return matched_name, goal_text


def _format_candidates_table(candidates: list[dict]) -> str:
    """Pretty-print candidates as a table for the LLM prompt."""
    header = f"{'ID':>4}  {'Material':<38}  {'Sy(MPa)':>8}  {'Cost($/kg)':>10}  {'Score':>6}  {'Temp:bonus':>10}  {'Fatigue':>8}  {'Pref':>6}"
    sep = "-" * len(header)
    rows = [header, sep]
    for c in candidates:
        rows.append(
            f"{c['material_id']:>4}  {c['name']:<38}  "
            f"{c['yield_strength_mpa']:>8}  {c['cost_usd_per_kg']:>10.2f}  "
            f"{c.get('score', 0):>6.1f}  "
            f"{c.get('temp_bonus', 1.0):>10.2f}  "
            f"{c.get('fatigue_bonus', 1.0):>8.2f}  "
            f"{c.get('preference_bonus', 1.0):>6.2f}"
        )
    return "\n".join(rows)


def _select_via_llm(
    candidates: list[dict],
    requirements,
    raw_input: str,
) -> dict:
    """Use the LLM to select the best material and generate justification.

    Returns a dict with keys: material_id, justification, runner_up_name,
    runner_up_reason, candidate_summary.
    """
    user_material, user_goal = _extract_user_material_preference(raw_input)
    factory = get_factory()

    prompt = _SELECTION_PROMPT.format(
        user_goal=user_goal,
        user_material=user_material or "None",
        spring_type=requirements.spring_type or "compression",
        candidates_table=_format_candidates_table(candidates),
        temp=requirements.operating_temperature_c or 25,
        corrosion="Yes" if requirements.corrosion_resistant else "No",
        cyclic="Yes" if requirements.cyclic_load else "No",
        cost_str=f"${requirements.max_cost:.2f}/kg" if hasattr(requirements, 'max_cost') and requirements.max_cost else "No limit",
    )

    messages = [
        SystemMessage(content=prompt),
        HumanMessage(content=json.dumps({
            "candidates": candidates,
            "requirements": requirements.model_dump(),
        }, indent=2)),
    ]

    for _ in range(len(factory._settings.llm_priority_order)):
        try:
            llm = factory.get_llm()
            response = llm.invoke(messages)
            raw = response.content.strip()
            # Strip markdown fences
            raw = re.sub(r"^```(?:json)?\s*", "", raw).rstrip("```").strip()
            selection: dict = json.loads(raw)
            return selection
        except Exception as exc:
            try:
                rotate_llm_on_quota_error(exc)
            except RuntimeError:
                logger.error("[Agent 3] All LLM providers exhausted.")
                break
            logger.warning("[Agent 3] LLM error (will retry): %s", exc)

    # Fallback: deterministic top pick
    top = candidates[0]
    return {
        "selected_material_id": top["material_id"],
        "justification": (
            f"Selected automatically: {top['name']} has the best "
            f"composite score ({top.get('score', 0):.1f}) across strength, "
            f"cost, temperature margin, and application fit."
        ),
        "runner_up_name": candidates[1]["name"] if len(candidates) > 1 else None,
        "runner_up_reason": None,
        "candidate_summary": f"{len(candidates)} candidate(s) evaluated.",
    }


# ── Main node ──────────────────────────────────────────────────────────────


def materials_engineer_node(state: AgentState) -> dict:
    """LangGraph node for Agent 3 – Materials Engineer."""
    logger.info("[Agent 3] Materials Engineer started.")

    requirements = state.get("requirements")
    if requirements is None:
        return _error(state, "MissingRequirements", "requirements is None")

    raw_input = state.get("_raw_input", "") or ""
    preferred_name, user_goal = _extract_user_material_preference(raw_input)
    if preferred_name:
        logger.info("[Agent 3] User preference detected: '%s'", preferred_name)

    # ── Call material query tool ───────────────────────────────────────────
    tool_input: dict[str, object] = {
        "operating_temperature_c": requirements.operating_temperature_c or 25.0,
        "corrosion_resistant": requirements.corrosion_resistant,
        "cyclic_load": requirements.cyclic_load,
        "spring_type": requirements.spring_type,
    }
    if preferred_name:
        tool_input["preferred_material_name"] = preferred_name

    # Si Agent 2 guardó min_yield_strength_mpa en state, usarlo como constraint.
    min_sy = state.get("min_yield_strength_mpa")
    if min_sy is not None:
        tool_input["min_yield_strength_mpa"] = float(min_sy)
        logger.info(
            "[Agent 3] Usando min_yield_strength_mpa=%.0f desde Agent 2.",
            float(min_sy),
        )

    try:
        result_json = query_material_properties_tool.invoke(tool_input)
        result = json.loads(result_json)
    except Exception as exc:
        return _error(state, type(exc).__name__, str(exc))

    # ── Handle no-match: find CLOSEST alternatives ─────────────────────────
    if result.get("status") == "no_match":
        logger.warning("[Agent 3] No material matches hard constraints.")
        body = _build_no_match_response(
            state, preferred_name, user_goal, requirements
        )
        # Return an error but include a rich message for the user
        msg = result.get("message", "No material satisfies all constraints.")
        return _error(state, "NoMaterialMatch", msg, user_message=body)

    if result.get("status") != "ok":
        return _error(state, "ToolError", result.get("message", "Unknown"))

    candidates: list[dict] = result["candidates"]

    # ── Always use LLM for engineering judgment ────────────────────────────
    selection = _select_via_llm(candidates, requirements, raw_input)

    sel_id = selection.get("selected_material_id")
    justification = selection.get("justification", "")
    runner_up_name = selection.get("runner_up_name")
    runner_up_reason = selection.get("runner_up_reason")
    candidate_summary = selection.get("candidate_summary", "")

    matching = [c for c in candidates if c["material_id"] == sel_id]
    selected = matching[0] if matching else candidates[0]

    material = MaterialProperties(**selected)

    # ── Build a rich user-facing message ───────────────────────────────────
    lines = [
        f"## Material seleccionado: {material.name}",
        "",
        f"| Propiedad | Valor |",
        f"|-----------|-------|",
        f"| Módulo de corte G | {material.shear_modulus_gpa} GPa |",
        f"| Límite elástico Sy | {material.yield_strength_mpa} MPa |",
        f"| Resistencia última Sut | {material.ultimate_strength_mpa} MPa |",
        f"| Temp. máxima | {material.max_temp_c}°C |",
        f"| Resistente a corrosión | {'Sí' if material.corrosion_resistant else 'No'} |",
        f"| Costo | ${material.cost_usd_per_kg:.2f}/kg |",
        f"| Score compuesto | {selected.get('score', 'N/A')} |",
        "",
        f"**Justificación:** {justification}",
    ]

    if runner_up_name and runner_up_reason:
        lines.append("")
        lines.append(f"**Alternativa:** {runner_up_name} — {runner_up_reason}")

    if candidate_summary:
        lines.append("")
        lines.append(f"_{candidate_summary}_")

    if preferred_name and preferred_name not in material.name.lower():
        lines.append("")
        lines.append(
            f"ℹ️ Nota: Mencionaste '{preferred_name}', pero "
            f"**{material.name}** es más adecuado para esta aplicación. "
            f"{justification.split('.')[0]}."
        )

    message_body = "\n".join(lines)

    logger.info(
        "[Agent 3] Selected: %s (G=%.1f, Sy=%.0f, $%.2f/kg, score=%s) | %s",
        material.name,
        material.shear_modulus_gpa,
        material.yield_strength_mpa,
        material.cost_usd_per_kg,
        selected.get("score", "?"),
        justification[:80],
    )

    return {
        "material": material,
        "current_step": "materials_engineer",
        "messages": [AIMessage(content=message_body)],
    }


# ── No-match handler ───────────────────────────────────────────────────────


def _build_no_match_response(
    state: AgentState,
    preferred_name: str | None,
    user_goal: str,
    requirements,
) -> str:
    """Generate a helpful response when no material satisfies constraints."""
    lines = [
        "## ⚠️ No se encontró un material que cumpla TODOS los requisitos",
        "",
        "### Restricciones activas",
        f"- Temperatura máxima: {requirements.operating_temperature_c or 25}°C",
        f"- Resistencia a corrosión: {'Sí' if requirements.corrosion_resistant else 'No'}",
        f"- Carga cíclica: {'Sí' if requirements.cyclic_load else 'No'}",
    ]
    if preferred_name:
        lines.append(f"- Preferencia del usuario: {preferred_name}")

    lines.extend([
        "",
        "### Posibles acciones",
        "- **Relajar temperatura**: reducir la exigencia de temperatura máxima permite más opciones",
        "- **Eliminar corrosión**: si el ambiente no es realmente corrosivo, se abre el catálogo",
        "- **Aumentar presupuesto**: algunos materiales cumplen todo pero son más caros",
    ])

    return "\n".join(lines)


# ── Error helper ───────────────────────────────────────────────────────────


def _error(
    state: AgentState,
    error_type: str,
    message: str,
    user_message: str | None = None,
) -> dict:
    logger.error("[Agent 3] %s: %s", error_type, message)
    result: dict = {
        "current_step": "materials_engineer_failed",
        "errors": state.get("errors", []) + [{
            "step": "materials_engineer",
            "error_type": error_type,
            "message": message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }],
    }
    if user_message:
        result["messages"] = [AIMessage(content=user_message)]
    return result
