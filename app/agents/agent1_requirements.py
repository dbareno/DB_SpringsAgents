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
import re
from datetime import datetime, timezone

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.types import interrupt

from app.core.llm_factory import get_factory, rotate_llm_on_quota_error
from app.schemas.state import AgentState, SpringType, UserRequirements
from app.schemas.structured_output import validate_requirement_extraction

logger = logging.getLogger(__name__)

# ── Clarification label mapping (moved from design_service's old ────────────
# concatenate-and-rerun path — the label helps the LLM parse free-text answers
# without depending on a rigid Q&A transcript format).
_CLARIFICATION_LABELS: list[tuple[str, str]] = [
    ("cíclica", "Cyclic load"),
    ("fatiga", "Cyclic load"),
    ("estática", "Cyclic load"),
    ("repetitivo", "Cyclic load"),
    ("ciclos de vida", "Cycles expected"),
    ("ciclos esperados", "Cycles expected"),
    ("compresión", "Spring type"),
    ("tracción", "Spring type"),
    ("torsión", "Spring type"),
    ("tipo de resorte", "Spring type"),
    ("spring type", "Spring type"),
    ("diámetro exterior", "Max outer diameter"),
    ("outer diameter", "Max outer diameter"),
    ("longitud libre", "Max free length"),
    ("free length", "Max free length"),
    ("temperatura", "Operating temperature"),
    ("temperature", "Operating temperature"),
    ("operating temp", "Operating temperature"),
    ("corrosivo", "Corrosion resistant"),
    ("corrosión", "Corrosion resistant"),
    ("deflexión", "Deflection"),
    ("deflection", "Deflection"),
    ("newton", "Load force"),
    ("newtons", "Load force"),
    ("fuerza", "Load force"),
    ("carga", "Load force"),
]


def _label_for_question(question: str) -> str:
    """Map a clarification question to a short explicit label."""
    lowered = question.lower()
    for keyword, label in _CLARIFICATION_LABELS:
        if keyword in lowered:
            return label
    return "Specification"

_SYSTEM_PROMPT = """You are a requirements analyst for spring design.
Extract ALL spring design parameters from the text below into the EXACT JSON field names provided.

The text may contain:
- An original user request in natural language
- Additional specifications listed as "Label: value" (e.g. "Load force: 500 N")

You MUST combine ALL information sources and extract every parameter you can find.
If both the original request and the additional specs mention the same parameter,
use the most specific value.

Return ONLY valid JSON — no markdown fences, no extra text.
Put null for values not mentioned. Infer spring_type from context
(compression/extension/torsion/spiral/wave). Default to compression if unclear.
Set corrosion_resistant and cyclic_load to false unless the user says otherwise.

Use the EXACT field names below. Set fields to null when the user did NOT
provide a value. NEVER invent or guess numeric values — if the user does not
specify it, it MUST be null.

Schema:
{
  "spring_type": "compression or extension or torsion or spiral or wave",
  "load_force_n": null,
  "deflection_mm": null,
  "spring_rate_n_mm": null,
  "max_outer_diameter_mm": null,
  "max_free_length_mm": null,
  "solid_length_mm": null,
  "operating_temperature_c": null,
  "corrosion_resistant": false,
  "cyclic_load": false,
  "cycles_expected": null,
  "clarification_questions": []
}
"""


def _extract_force(text: str) -> float | None:
    """
    Extrae fuerza en Newtons desde texto plano mediante regex.
    Usado como fallback cuando el LLM no pobló el campo JSON.
    """
    if not text:
        return None
    patterns = [
        # "500N", "500 N", "500 Newtons", "500 newtons"
        r'(\d+(?:\.\d+)?)\s*N(?:ewtons?)?\b',
        # "Load force: 500 N", "Load force:500N" (formato etiqueta inglés)
        r'(?:Load force|load force|Force|force)\s*:\s*(\d+(?:\.\d+)?)',
        # "fuerza: 500", "fuerza de 500"
        r'fuerza\s*(?:de\s*)?(\d+(?:\.\d+)?)',
        # "carga: 500", "carga de 500"
        r'carga\s*(?:de\s*)?(\d+(?:\.\d+)?)',
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            return float(m.group(1))
    return None


def _extract_deflection(text: str) -> float | None:
    """
    Extrae deflexión en mm desde texto plano mediante regex.
    Usado como fallback cuando el LLM no pobló el campo JSON.
    """
    if not text:
        return None
    patterns = [
        # "deflexión: 10", "deflexión de 10"
        r'deflexi[oó]n\s*(?:de\s*)?(\d+(?:\.\d+)?)',
        # "Deflection: 10 mm", "deflection: 10mm" (formato etiqueta inglés)
        r'(?:Deflection|deflection)\s*:?\s*(\d+(?:\.\d+)?)\s*mm',
        # "recorrido: 10", "recorrido de 10"
        r'recorrido\s*(?:de\s*)?(\d+(?:\.\d+)?)',
        # "10mm deflexión", "10 mm de deflexión"
        r'(\d+(?:\.\d+)?)\s*mm\s*(?:de\s*)?(?:deflexi[oó]n|recorrido|deflection)',
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            return float(m.group(1))
    return None


def _extract_rate(text: str) -> float | None:
    """
    Extrae tasa elástica (N/mm) desde texto plano mediante regex.
    """
    if not text:
        return None
    patterns = [
        r'(?:rate|spring\s*rate|rigidez)\s*:?\s*(\d+(?:\.\d+)?)\s*N/mm',
        r'(\d+(?:\.\d+)?)\s*N/mm',
        r'rigidez\s*(?:de\s*)?(\d+(?:\.\d+)?)',
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            return float(m.group(1))
    return None


def _extract_outer_diameter(text: str) -> float | None:
    """
    Extrae diámetro exterior máximo (mm) desde texto plano.
    """
    if not text:
        return None
    patterns = [
        r'(?:di[aá]metro\s*exterior|outer\s*diameter|OD)\s*:?\s*(\d+(?:\.\d+)?)\s*mm',
        r'(?:di[aá]metro\s*exterior|outer\s*diameter)\s*(?:m[aá]x(?:imo)?)?\s*(?:de\s*)?(\d+(?:\.\d+)?)\s*mm',
        r'OD\s*(?:m[aá]x)?\s*(\d+(?:\.\d+)?)\s*mm',
        r'(\d+(?:\.\d+)?)\s*mm\s*(?:de\s*)?(?:di[aá]metro\s*)?(?:exterior|outer)',
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            return float(m.group(1))
    return None


def _extract_free_length(text: str) -> float | None:
    """
    Extrae longitud libre máxima (mm) desde texto plano.
    """
    if not text:
        return None
    patterns = [
        r'(?:longitud\s*libre|free\s*length|Lf)\s*:?\s*(\d+(?:\.\d+)?)\s*mm',
        r'(?:longitud|length)\s*(?:libre|free)?\s*(?:m[aá]x(?:imo)?)?\s*(?:de\s*)?(\d+(?:\.\d+)?)\s*mm',
        r'(\d+(?:\.\d+)?)\s*mm\s*(?:de\s*)?(?:longitud\s*)?(?:libre|free)',
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            return float(m.group(1))
    return None


def _extract_temperature(text: str) -> float | None:
    """
    Extrae temperatura de operación (°C) desde texto plano.
    """
    if not text:
        return None
    patterns = [
        r'(?:temperatura|operating\s*temp|temperature)\s*(?:de\s*operaci[oó]n)?\s*:?\s*(\d+(?:\.\d+)?)\s*°?C',
        r'(\d+(?:\.\d+)?)\s*°?C',
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            return float(m.group(1))
    return None


def _infer_spring_type(text: str) -> str | None:
    """
    Intenta inferir el tipo de resorte desde el texto.
    Returns None si no puede determinarlo.
    """
    if not text:
        return None
    text_lower = text.lower()
    # Mapas de palabras clave a tipo
    compression = ["compresión", "compresion", "compression"]
    extension = ["tracción", "traccion", "traction", "extension", "extensión"]
    torsion = ["torsión", "torsion", "torsional"]
    
    if any(k in text_lower for k in compression):
        return "compression"
    if any(k in text_lower for k in extension):
        return "extension"
    if any(k in text_lower for k in torsion):
        return "torsion"
    return None


def _extract_cycles(text: str) -> int | None:
    """
    Extrae ciclos esperados desde texto plano.
    """
    if not text:
        return None
    patterns = [
        r'(?:ciclos|cycles)\s*(?:de\s*vida)?\s*:?\s*(\d+)',
        r'(\d+)\s*(?:ciclos|cycles)',
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            return int(m.group(1))
    return None


def _check_corrosion(text: str) -> bool | None:
    """
    Detecta si el usuario menciona ambiente corrosivo.
    Reconoce texto natural y formato de etiqueta "Corrosion resistant: no".
    Returns True si corrosivo, False si no, None si no se menciona.
    """
    if not text:
        return None
    text_lower = text.lower()
    # Formato etiqueta
    if re.search(r'corrosion\s*resistant\s*:?\s*(?:no|false)', text_lower):
        return False
    if re.search(r'corrosion\s*resistant\s*:?\s*(?:sí|si|yes|true)', text_lower):
        return True
    # Texto natural negativo
    negative = ["no corrosivo", "sin corrosión", "no corrosion",
                "ambiente normal", "seco", "no está expuesto"]
    if any(k in text_lower for k in negative):
        return False
    # Texto natural positivo
    positive = ["corrosivo", "corrosión", "corrosion",
                "ácido", "ácida", "salino", "marino", "expuesto a"]
    if any(k in text_lower for k in positive):
        return True
    return None


def _check_cyclic(text: str) -> bool | None:
    """
    Detecta si el usuario menciona carga cíclica/fatiga.
    Reconoce texto natural y formato de etiqueta.
    """
    if not text:
        return None
    text_lower = text.lower()
    # Formato etiqueta
    if re.search(r'cyclic\s*load\s*:?\s*(?:no|static|false)', text_lower):
        return False
    if re.search(r'cyclic\s*load\s*:?\s*(?:yes|cyclic|fatigue|true)', text_lower):
        return True
    # Texto natural
    if any(k in text_lower for k in ["estático", "static", "una sola vez"]):
        return False
    if any(k in text_lower for k in ["cíclico", "ciclico", "fatiga", "cyclic",
                                      "repetitivo", "alternante"]):
        return True
    return None


def _derive_values(data: dict) -> None:
    """
    Deriva valores calculables a partir de los datos disponibles.
    Se ejecuta DESPUÉS de la extracción por regex y ANTES de generar preguntas.

    Relaciones:
      - k = F / δ   (tasa elástica = carga / deflexión)
      - F = k * δ   (carga = tasa * deflexión)
      - δ = F / k   (deflexión = carga / tasa)

    Si tenemos 2 de {F, δ, k}, podemos calcular el 3ro.
    """
    f = data.get("load_force_n")
    d = data.get("deflection_mm")
    k = data.get("spring_rate_n_mm")

    # k = F / δ   (si tenemos carga y deflexión, la tasa se calcula)
    if k is None and f is not None and d is not None and d > 0:
        data["spring_rate_n_mm"] = round(f / d, 4)
        logger.info("[Agent 1] Derived k=%.2f N/mm from F=%.1f / δ=%.1f", f / d, f, d)

    # F = k * δ   (si tenemos tasa y deflexión, la carga se calcula)
    if f is None and k is not None and d is not None:
        data["load_force_n"] = round(k * d, 2)
        logger.info("[Agent 1] Derived F=%.1f N from k=%.2f * δ=%.1f", k * d, k, d)

    # δ = F / k   (si tenemos carga y tasa, la deflexión se calcula)
    if d is None and f is not None and k is not None and k > 0:
        data["deflection_mm"] = round(f / k, 4)
        logger.info("[Agent 1] Derived δ=%.1f mm from F=%.1f / k=%.2f", f / k, f, k)

    # Nota: OD, free length, temperatura, corrosión y ciclos NO se pueden
    # calcular a partir de otras variables — son inputs de usuario necesarios.


def _determine_completeness(data: dict, raw_input: str = "") -> tuple[bool, list[str]]:
    """
    Evalúa completitud y genera preguntas para TODAS las variables de diseño
    de resorte que falten, no solo las críticas.

    Variables evaluadas:
      - Tipo de resorte (spring_type)
      - Carga (load_force_n) o tasa elástica (spring_rate_n_mm)
      - Deflexión (deflection_mm) o tasa elástica (spring_rate_n_mm)
      - Diámetro exterior máximo (max_outer_diameter_mm)
      - Longitud libre máxima (max_free_length_mm)
      - Temperatura de operación (operating_temperature_c)
      - Ambiente corrosivo (corrosion_resistant)
      - Carga cíclica y ciclos esperados (cyclic_load / cycles_expected)

    Estrategia:
      1. Regex authority — extraer o limpiar cada campo (anular alucinaciones)
      2. Derivación — si tenemos 2 de {F, δ, k}, calcular el 3ro
      3. Preguntar solo por lo que NO se puede calcular ni derivar

    Returns (is_complete, clarification_questions).
    """
    # ── 1. Regex authority: extraer o limpiar cada campo ──────────────
    if raw_input:
        force = _extract_force(raw_input)
        data["load_force_n"] = force if force is not None else data.pop("load_force_n", None)

        deflection = _extract_deflection(raw_input)
        data["deflection_mm"] = deflection if deflection is not None else data.pop("deflection_mm", None)

        rate_val = _extract_rate(raw_input)
        data["spring_rate_n_mm"] = rate_val if rate_val is not None else data.pop("spring_rate_n_mm", None)

        od = _extract_outer_diameter(raw_input)
        data["max_outer_diameter_mm"] = od if od is not None else data.pop("max_outer_diameter_mm", None)

        fl = _extract_free_length(raw_input)
        data["max_free_length_mm"] = fl if fl is not None else data.pop("max_free_length_mm", None)

        temp = _extract_temperature(raw_input)
        data["operating_temperature_c"] = temp if temp is not None else data.pop("operating_temperature_c", None)

        inferred_type = _infer_spring_type(raw_input)
        current_type = data.get("spring_type", "unknown")
        if inferred_type and (current_type in ("unknown", None)):
            data["spring_type"] = inferred_type

        corrosion = _check_corrosion(raw_input)
        if corrosion is not None:
            data["corrosion_resistant"] = corrosion

        cyclic = _check_cyclic(raw_input)
        if cyclic is not None:
            data["cyclic_load"] = cyclic

        cycles = _extract_cycles(raw_input)
        data["cycles_expected"] = cycles if cycles is not None else data.pop("cycles_expected", None)

    # ── 2. DEBUG: mostrar qué extrajo el regex ────────────────────────
    if raw_input:
        lines = raw_input.strip().split("\n")
        preview = "\n".join(lines[-10:])  # últimas 10 líneas (las etiquetas)
        logger.info("[Agent 1] === RAW INPUT (last 10 lines) ===\n%s", preview)
        logger.info(
            "[Agent 1] Regex extracted — F=%s, δ=%s, k=%s, OD=%s, FL=%s, T=%s, corr=%s, cyclic=%s, cycles=%s",
            data.get("load_force_n"),
            data.get("deflection_mm"),
            data.get("spring_rate_n_mm"),
            data.get("max_outer_diameter_mm"),
            data.get("max_free_length_mm"),
            data.get("operating_temperature_c"),
            data.get("corrosion_resistant"),
            data.get("cyclic_load"),
            data.get("cycles_expected"),
        )

    # ── 3. Derivar valores calculables ────────────────────────────────
    _derive_values(data)

    # ── 4. Estado actual de cada campo ────────────────────────────────
    h_type = data.get("spring_type", "unknown") not in ("unknown", None)
    h_load = data.get("load_force_n") is not None
    h_rate = data.get("spring_rate_n_mm") is not None
    h_deflection = data.get("deflection_mm") is not None
    h_od = data.get("max_outer_diameter_mm") is not None
    h_fl = data.get("max_free_length_mm") is not None
    h_temp = data.get("operating_temperature_c") is not None
    h_corrosion_specified = data.get("corrosion_resistant") is not None  # True si el usuario dijo sí O no
    h_cyclic = data.get("cyclic_load")  # True/False/None
    h_cycles = data.get("cycles_expected") is not None

    # ── 5. Generar preguntas solo para lo que NO se puede derivar ────
    questions: list[str] = []
    derived: list[str] = []  # para informar lo que se calculó

    if not h_type:
        questions.append("¿Qué tipo de resorte es? (compresión, tracción o torsión)")

    # Para {carga, deflexión, tasa} solo preguntar si faltan 2 o más
    # (con 1 podemos derivar el resto)
    known_mech = sum([h_load, h_deflection, h_rate])
    if known_mech < 2:
        if not h_load and not h_rate:
            questions.append("¿Qué fuerza de carga (en Newtons) debe soportar el resorte?")
        if not h_deflection and not h_rate:
            questions.append("¿Cuánta deflexión (en mm) necesita el resorte?")
        if not h_rate and not h_load and not h_deflection:
            questions.append("¿Cuál es la tasa elástica (N/mm) del resorte? (si tiene carga y deflexión se calcula automáticamente)")

    # Informar lo que se derivó
    if h_rate and known_mech >= 2:
        f = data.get("load_force_n")
        d = data.get("deflection_mm")
        k = data.get("spring_rate_n_mm")
        if f is not None and d is not None:
            derived.append(f"Tasa elástica: {k:.2f} N/mm (calculada de F={f:.0f}N / δ={d:.1f}mm)")

    # OD, free length, temp — NO se pueden derivar
    if not h_od:
        questions.append("¿Cuál es el diámetro exterior máximo disponible (en mm) para el resorte?")

    if not h_fl:
        questions.append("¿Cuál es la longitud libre máxima disponible (en mm) para el resorte?")

    if not h_temp:
        questions.append("¿Cuál es la temperatura de operación (en °C)? (opcional — default 20°C)")

    # Corrosión: preguntar solo si el usuario NO respondió sí ni no
    if not h_corrosion_specified:
        questions.append("¿El resorte estará expuesto a un ambiente corrosivo? (opcional)")

    # Carga cíclica: preguntar solo si el usuario NO dijo si es cíclica o estática
    if h_cyclic is None:
        questions.append("¿La carga es cíclica (fatiga) o estática? (opcional)")
    elif h_cyclic is True and not h_cycles:
        questions.append("¿Cuántos ciclos de vida espera? (opcional)")

    # ── 5. Registrar derivaciones en log ──────────────────────────────
    for msg in derived:
        logger.info("[Agent 1] %s", msg)

    # ── 7. Decisión de completitud ────────────────────────────────────
    # Mínimo para diseñar: (carga o tasa) Y (deflexión o tasa) Y tipo conocido
    # El resto (OD, free length, temp, corrosión, ciclos) se puede asumir
    # con valores por defecto si el usuario no los especifica.
    is_complete = (h_load or h_rate) and (h_deflection or h_rate) and h_type

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
        logger.warning("[Agent 1] _raw_input MISSING from state — falling back to message history.")
        for msg in reversed(state.get("messages", [])):
            if isinstance(msg, HumanMessage):
                raw_input = str(msg.content)
                logger.info("[Agent 1] Fallback found HumanMessage (%d chars).", len(raw_input))
                break
        else:
            logger.warning("[Agent 1] No HumanMessage in message history either — raw_input will be empty.")

    logger.info("[Agent 1] raw_input length=%d, starts_with=%s",
                len(raw_input), repr(raw_input[:80]))

    session_answers: dict[str, str] = dict(state.get("session_answers", {}))
    max_attempts = len(factory._priority_order) + 1
    base_raw_input = state.get("_raw_input", raw_input)

    # ── Multi-turn conversation (Phase 3) ───────────────────────────────────
    # LangGraph replays a node's ENTIRE function body from the top on every
    # resume — there is no way to "skip ahead" past earlier code. Each
    # ``interrupt()`` call that has ALREADY been resumed once simply returns
    # its resume value without re-pausing; only a call site reached for the
    # FIRST time (fresh, not-yet-resumed) actually pauses the graph. So a
    # loop with ONE interrupt() call per round, resumed via
    # ``Command(resume=answers)``, is the correct — and LangGraph's
    # officially documented — pattern for asking several sequential
    # follow-up questions within a single node: round 0's interrupt resumes
    # immediately (returning the round-0 answer) and round 1's interrupt
    # actually pauses (it has never been reached before), and so on.
    #
    # Cost trade-off: every resume REPLAYS all already-answered rounds'
    # LLM calls too (they resume immediately without pausing) — this is
    # inherent to LangGraph's interrupt model, not a bug. Bounded by
    # ``max_rounds`` to cap worst-case replay cost and prevent infinite
    # clarification loops.
    max_rounds = 5
    for _round in range(max_rounds):
        if session_answers:
            labeled = [
                f"{_label_for_question(q)}: {a}"
                for q, a in session_answers.items()
                if a
            ]
            raw_input = (
                f"{base_raw_input}\n\n"
                f"--- Additional specifications provided ---\n"
                + "\n".join(f"  - {line}" for line in labeled)
            )

        messages = [
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=raw_input),
        ]
        requirements, response, error_result = _extract_requirements(
            factory=factory,
            messages=messages,
            raw_input=raw_input,
            max_attempts=max_attempts,
            state=state,
        )
        if error_result is not None:
            return error_result

        logger.info(
            "[Agent 1] Extraction complete (round %d). is_complete=%s, spring_type=%s, questions=%d",
            _round,
            requirements.is_complete,
            requirements.spring_type,
            len(requirements.clarification_questions),
        )

        if requirements.is_complete:
            return {
                "requirements": requirements,
                "current_step": "requirements_analyst",
                "messages": [response],
                "interrupted": False,
                "session_answers": session_answers,
            }

        # ── Requirements incomplete: PAUSE the graph at this exact point
        # instead of returning a terminal "needs_clarification" state.
        # ``interrupt()`` raises GraphInterrupt (surfacing `questions` to
        # the caller) on first reach; once the caller resumes via
        # ``Command(resume=answers)``, THIS specific call returns
        # `answers` directly (no graph replay from START needed).
        for qi, q in enumerate(requirements.clarification_questions, 1):
            logger.info("[Agent 1]   Q%d: %s", qi, q)

        answers = interrupt(
            {
                "type": "clarification_needed",
                "questions": requirements.clarification_questions,
                "partial_requirements": requirements.model_dump(),
            }
        )
        answers = answers if isinstance(answers, dict) else {}
        session_answers = {**session_answers, **answers}
        logger.info(
            "[Agent 1] Resumed (round %d) with %d accumulated answer(s).",
            _round,
            len(session_answers),
        )

    return _build_error(
        state, "ClarificationRoundLimit", f"Exceeded {max_rounds} clarification rounds."
    )


def _extract_requirements(
    factory: object,
    messages: list,
    raw_input: str,
    max_attempts: int,
    state: AgentState,
) -> tuple[UserRequirements | None, object, dict | None]:
    """
    Run the LLM extraction + programmatic completeness check for ONE
    conversation round, retrying across LLM providers on quota errors.

    Returns ``(requirements, response, None)`` on success, or
    ``(None, None, error_dict)`` if all providers/attempts were exhausted —
    callers should return ``error_dict`` directly from the node.
    """
    for attempt in range(max_attempts):
        try:
            llm = factory.get_llm()  # type: ignore[attr-defined]
            response = llm.invoke(messages)
            raw_json = response.content.strip()

            # Strip accidental markdown fences
            if raw_json.startswith("```"):
                raw_json = raw_json.split("```")[1].lstrip("json").strip()

            data = json.loads(raw_json)

            # ── ADR-6: Validate structured output with best-effort fallback ────
            # strict=False allows field coercion and filling when LLM omits fields
            is_schema_valid, cleaned_data, schema_errors = validate_requirement_extraction(
                data, strict=False
            )
            if schema_errors:
                logger.warning(
                    "[Agent 1] Schema validation issues (non-strict): %s",
                    "; ".join(schema_errors)
                )
            data = cleaned_data

            # ── Override: programmatic completeness, not LLM's guess ──────
            # Pass raw_input so regex fallback can extract values the LLM missed
            is_complete, questions = _determine_completeness(data, raw_input=raw_input)
            data["is_complete"] = is_complete
            data["clarification_questions"] = questions

            requirements = UserRequirements(raw_input=raw_input, **data)
            return requirements, response, None

        except json.JSONDecodeError:
            logger.error("[Agent 1] LLM returned invalid JSON on attempt %d/%d", attempt + 1, max_attempts)
            if attempt < max_attempts - 1:
                continue
            return None, None, _build_error(
                state, "InvalidJSON", "LLM returned invalid JSON after all attempts"
            )

        except Exception as exc:
            try:
                rotate_llm_on_quota_error(exc)
                logger.warning("[Agent 1] Rotated LLM after error: %s", exc)
                continue
            except RuntimeError as all_exhausted:
                return None, None, _build_error(
                    state, type(exc).__name__, str(all_exhausted)
                )
            except Exception as non_quota_error:
                logger.warning(
                    "[Agent 1] Non-quota error on attempt %d/%d: %s",
                    attempt + 1, max_attempts, non_quota_error,
                )
                if attempt < max_attempts - 1:
                    if hasattr(factory, 'next_provider'):
                        factory.next_provider()
                    continue
                return None, None, _build_error(
                    state, type(non_quota_error).__name__, str(non_quota_error)
                )

    # Defensive fallback — should be unreachable (loop always returns).
    return None, None, _build_error(
        state, "ExhaustedAttempts", "LLM extraction failed after all retry attempts."
    )


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
