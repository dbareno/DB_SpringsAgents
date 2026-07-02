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

_SYSTEM_PROMPT = """Extract spring design parameters from the user's text.
Return ONLY valid JSON — no markdown fences, no extra text.

Put null for values not mentioned. Infer spring_type from context
(compression/extension/torsion/spiral/wave). Default to compression if unclear.
Set corrosion_resistant and cyclic_load to false unless the user says otherwise.

JSON:
{
  "spring_type": "...",
  "load_force_n": <float or null>,
  "deflection_mm": <float or null>,
  "spring_rate_n_mm": <float or null>,
  "max_outer_diameter_mm": <float or null>,
  "max_free_length_mm": <float or null>,
  "solid_length_mm": <float or null>,
  "operating_temperature_c": <float or null>,
  "corrosion_resistant": <false>,
  "cyclic_load": <false>,
  "cycles_expected": <int or null>,
  "clarification_questions": []
}"""


def _determine_completeness(data: dict) -> tuple[bool, list[str]]:
    """
    Determine if requirements are complete enough for design, and generate
    clarification questions for missing critical fields.

    Critical fields:
      - load_force_n OR spring_rate_n_mm
      - deflection_mm OR spring_rate_n_mm

    Returns (is_complete, clarification_questions).
    """
    has_load = data.get("load_force_n") is not None
    has_rate = data.get("spring_rate_n_mm") is not None
    has_deflection = data.get("deflection_mm") is not None
    spring_type = data.get("spring_type", "unknown")

    questions: list[str] = []

    if not has_load and not has_rate:
        questions.append("What load force (in Newtons) does the spring need to support?")
    elif not has_load:
        questions.append("What load force (in Newtons) does the spring need to support?")

    if not has_deflection and not has_rate:
        questions.append("How much deflection (in mm) do you need?")

    if spring_type in ("unknown", "unknown"):
        questions.append("What type of spring is this? (compression, extension, or torsion)")

    # For a valid design we need: (load OR rate) AND (deflection OR rate)
    is_complete = (has_load or has_rate) and (has_deflection or has_rate)

    return is_complete, questions


def requirements_analyst_node(state: AgentState) -> dict:
    """
    LangGraph node function for Agent 1.
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

    max_attempts = len(factory._priority_order) + 1
    for attempt in range(max_attempts):
        try:
            llm = factory.get_llm()
            response = llm.invoke(messages)
            raw_json = response.content.strip()

            # Strip accidental markdown fences
            if raw_json.startswith("```"):
                raw_json = raw_json.split("```")[1].lstrip("json").strip()

            data = json.loads(raw_json)

            # ── Override: programmatic completeness, not LLM's guess ──────
            is_complete, questions = _determine_completeness(data)
            data["is_complete"] = is_complete
            data["clarification_questions"] = questions

            requirements = UserRequirements(raw_input=raw_input, **data)

            logger.info(
                "[Agent 1] Extraction complete. is_complete=%s, spring_type=%s, questions=%d",
                requirements.is_complete,
                requirements.spring_type,
                len(requirements.clarification_questions),
            )

            return {
                "requirements": requirements,
                "current_step": "requirements_analyst",
                "messages": [response],
            }

        except json.JSONDecodeError:
            logger.error("[Agent 1] LLM returned invalid JSON on attempt %d/%d", attempt + 1, max_attempts)
            if attempt < max_attempts - 1:
                continue
            return _build_error(state, "InvalidJSON", "LLM returned invalid JSON after all attempts")

        except Exception as exc:
            try:
                rotate_llm_on_quota_error(exc)
                logger.warning("[Agent 1] Rotated LLM after error: %s", exc)
                continue
            except RuntimeError as all_exhausted:
                return _build_error(state, type(exc).__name__, str(all_exhausted))
            except Exception as non_quota_error:
                logger.warning(
                    "[Agent 1] Non-quota error on attempt %d/%d: %s",
                    attempt + 1, max_attempts, non_quota_error,
                )
                if attempt < max_attempts - 1:
                    if hasattr(factory, 'next_provider'):
                        factory.next_provider()
                    continue
                return _build_error(state, type(non_quota_error).__name__, str(non_quota_error))


def _build_error(state: AgentState, error_type: str, message: str) -> dict:
    """Build error return dict for Agent 1 failures."""
    logger.error("[Agent 1] %s: %s", error_type, message)
    return {
        "current_step": "requirements_analyst_failed",
        "errors": state.get("errors", []) + [{
            "step": "requirements_analyst",
            "error_type": error_type,
            "message": message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }],
    }
