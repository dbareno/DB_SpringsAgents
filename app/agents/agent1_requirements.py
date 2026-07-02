"""
app/agents/agent1_requirements.py
─────────────────────────────────────────────────────────────────────────────
Agent 1 – Requirements Analyst

Responsibilities
────────────────
* Accept any natural-language input (from ultra-precise specs to vague
  descriptions like "I need a small spring for my mechanical pen").
* Extract structured fields into ``UserRequirements`` via an LLM with a
  strict JSON-output prompt.
* If critical fields are missing, generate ``clarification_questions`` and
  set ``is_complete = False`` so the Orchestrator can halt and ask the user.
* When sufficient, set ``is_complete = True`` to advance the graph.

LLM fallback
────────────
Catches quota errors and calls ``rotate_llm_on_quota_error`` so the graph
automatically retries with the next provider.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from langchain_core.messages import HumanMessage, SystemMessage

from app.core.llm_factory import get_factory, rotate_llm_on_quota_error
from app.schemas.state import AgentState, SpringType, UserRequirements

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a mechanical engineering requirements analyst.
Your task is to extract structured spring design parameters from the user's
natural-language input.

RULES:
1. Return ONLY valid JSON — no markdown fences, no extra text.
2. If a value is not mentioned, set it to null.
3. Infer spring_type from context clues (compression, extension, torsion,
   spiral, wave). If unclear, use "unknown".
4. Set is_complete to false if any of these are missing:
   - load_force_n OR spring_rate_n_mm
   - deflection_mm OR spring_rate_n_mm
   If all critical parameters can be reasonably inferred, set is_complete to true.
5. Generate 1–3 targeted clarification_questions for each missing critical field.

JSON schema to return:
{
  "spring_type": "<compression|extension|torsion|spiral|wave|unknown>",
  "load_force_n": <float|null>,
  "deflection_mm": <float|null>,
  "spring_rate_n_mm": <float|null>,
  "max_outer_diameter_mm": <float|null>,
  "max_free_length_mm": <float|null>,
  "solid_length_mm": <float|null>,
  "operating_temperature_c": <float|null>,
  "corrosion_resistant": <bool>,
  "cyclic_load": <bool>,
  "cycles_expected": <int|null>,
  "clarification_questions": [<string>, ...],
  "is_complete": <bool>
}"""


def requirements_analyst_node(state: AgentState) -> dict:
    """
    LangGraph node function for Agent 1.

    Args:
        state: Current graph state.

    Returns:
        Partial state dict with updated ``requirements``, ``current_step``,
        ``messages``, and ``errors``.
    """
    logger.info("[Agent 1] Requirements Analyst started.")
    factory = get_factory()

    # Grab raw input: prefer the dedicated field, fall back to last human msg
    raw_input: str = state.get("_raw_input", "")
    if not raw_input:
        for msg in reversed(state.get("messages", [])):
            if isinstance(msg, HumanMessage):
                raw_input = str(msg.content)
                break

    messages = [
        SystemMessage(content=_SYSTEM_PROMPT),
        HumanMessage(content=raw_input),
    ]

    for attempt in range(factory._settings.llm_max_tokens):  # use retry budget
        try:
            llm = factory.get_llm()
            response = llm.invoke(messages)
            raw_json = response.content.strip()

            # Strip accidental markdown fences
            if raw_json.startswith("```"):
                raw_json = raw_json.split("```")[1].lstrip("json").strip()

            data = json.loads(raw_json)
            requirements = UserRequirements(raw_input=raw_input, **data)

            logger.info(
                "[Agent 1] Extraction complete. is_complete=%s, spring_type=%s",
                requirements.is_complete,
                requirements.spring_type,
            )

            return {
                "requirements": requirements,
                "current_step": "requirements_analyst",
                "messages": [response],
            }

        except Exception as exc:
            try:
                factory = rotate_llm_on_quota_error(exc)
                logger.warning("[Agent 1] Rotated LLM after error: %s", exc)
                continue
            except RuntimeError as all_exhausted:
                error_entry = {
                    "step": "requirements_analyst",
                    "error_type": type(exc).__name__,
                    "message": str(all_exhausted),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                logger.error("[Agent 1] All providers exhausted: %s", all_exhausted)
                return {
                    "current_step": "requirements_analyst_failed",
                    "errors": state.get("errors", []) + [error_entry],
                }
        break  # only runs if no exception
