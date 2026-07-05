"""
app/schemas/structured_output.py
─────────────────────────────────────────────────────────────────────────────
JSON schema definitions for structured LLM output validation (ADR-6).

Provides:
- JSON schema for requirement extraction (UserRequirements)
- Strict validation mode that enforces schema conformance
- Fallback to best-effort parsing when strict mode is disabled
- Validation utilities for other agent outputs (rationale, options, etc.)
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# JSON Schema for UserRequirements extraction (Agent 1)
# ─────────────────────────────────────────────────────────────────────────────

REQUIREMENT_EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "spring_type": {
            "type": "string",
            "enum": ["compression", "extension", "torsion", "spiral", "wave", "unknown"],
            "description": "Type of spring being designed",
        },
        "load_force_n": {
            "type": ["number", "null"],
            "description": "Required force in Newtons",
        },
        "deflection_mm": {
            "type": ["number", "null"],
            "description": "Required deflection in mm",
        },
        "spring_rate_n_mm": {
            "type": ["number", "null"],
            "description": "Spring rate k = F/x in N/mm",
        },
        "max_outer_diameter_mm": {
            "type": ["number", "null"],
            "description": "OD constraint in mm",
        },
        "max_free_length_mm": {
            "type": ["number", "null"],
            "description": "Maximum free length in mm",
        },
        "solid_length_mm": {
            "type": ["number", "null"],
            "description": "Solid (compressed) length in mm",
        },
        "operating_temperature_c": {
            "type": ["number", "null"],
            "description": "Max operating temperature in °C",
        },
        "corrosion_resistant": {
            "type": "boolean",
            "description": "Corrosion resistance required",
        },
        "cyclic_load": {
            "type": "boolean",
            "description": "True if fatigue life matters",
        },
        "cycles_expected": {
            "type": ["integer", "null"],
            "description": "Expected fatigue cycles",
        },
        "clarification_questions": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Open questions when requirements are ambiguous",
        },
        "is_complete": {
            "type": "boolean",
            "description": "True when inputs are sufficient for design",
        },
    },
    "required": [
        "spring_type",
        "load_force_n",
        "deflection_mm",
        "spring_rate_n_mm",
        "max_outer_diameter_mm",
        "max_free_length_mm",
        "solid_length_mm",
        "operating_temperature_c",
        "corrosion_resistant",
        "cyclic_load",
        "cycles_expected",
        "clarification_questions",
        "is_complete",
    ],
}


# ─────────────────────────────────────────────────────────────────────────────
# Validation utilities (ADR-6)
# ─────────────────────────────────────────────────────────────────────────────


def validate_requirement_extraction(
    data: dict[str, Any],
    strict: bool = False,
) -> tuple[bool, dict[str, Any], list[str]]:
    """
    Validate LLM-extracted requirements against schema.

    Args:
        data: Parsed JSON from LLM
        strict: If True, reject any schema violations. If False, try best-effort
                field recovery (fill missing fields with None, coerce types).

    Returns:
        (is_valid, cleaned_data, error_messages)
        - is_valid: True if schema-compliant or recoverable
        - cleaned_data: Coerced/filled data structure
        - error_messages: List of issues encountered (empty if valid)
    """
    errors: list[str] = []
    cleaned: dict[str, Any] = dict(data) if data else {}

    # Field-by-field validation with best-effort cleanup
    schema_props = REQUIREMENT_EXTRACTION_SCHEMA["properties"]
    required_fields = REQUIREMENT_EXTRACTION_SCHEMA["required"]

    for field_name, field_spec in schema_props.items():
        field_type = field_spec.get("type")
        field_value = cleaned.get(field_name)

        # Handle missing fields
        if field_name not in cleaned:
            if field_name in required_fields:
                if strict:
                    errors.append(f"Missing required field: {field_name}")
                else:
                    # Best-effort: fill with sensible default
                    if "boolean" in str(field_type):
                        cleaned[field_name] = False
                    elif "array" in str(field_type):
                        cleaned[field_name] = []
                    else:
                        cleaned[field_name] = None
                    logger.debug("[StructuredOutput] Filled missing field %s with default", field_name)
            continue

        # Type validation and coercion
        if field_value is None:
            # None is allowed for nullable types
            if isinstance(field_type, list) and "null" in field_type:
                continue
            elif not isinstance(field_type, list) and field_type == "null":
                continue
            elif strict:
                errors.append(f"Field {field_name} is null but schema does not allow it")
            # Non-strict: leave as None
            continue

        # Coerce types if needed
        if isinstance(field_type, list):
            allowed_types = [t for t in field_type if t != "null"]
            if not allowed_types:
                continue
            target_type = allowed_types[0]
        else:
            target_type = field_type

        try:
            if target_type == "string" and not isinstance(field_value, str):
                cleaned[field_name] = str(field_value)
            elif target_type == "number":
                if not isinstance(field_value, (int, float)):
                    cleaned[field_name] = float(field_value)
                else:
                    cleaned[field_name] = float(field_value)
            elif target_type == "integer":
                if not isinstance(field_value, int):
                    cleaned[field_name] = int(float(field_value))
                else:
                    cleaned[field_name] = int(field_value)
            elif target_type == "boolean":
                if isinstance(field_value, bool):
                    pass
                elif isinstance(field_value, str):
                    cleaned[field_name] = field_value.lower() in ("true", "yes", "1")
                else:
                    cleaned[field_name] = bool(field_value)
            elif target_type == "array":
                if not isinstance(field_value, list):
                    cleaned[field_name] = [field_value]
        except (ValueError, TypeError) as exc:
            if strict:
                errors.append(f"Field {field_name}: cannot coerce to {target_type}: {exc}")
            else:
                logger.debug("[StructuredOutput] Could not coerce %s to %s, leaving as-is", field_name, target_type)

    if strict and errors:
        return False, cleaned, errors

    return True, cleaned, errors


def format_requirement_schema_prompt() -> str:
    """
    Return a prompt instruction block for LLM structured output (for Agent 1).
    Suitable for inclusion in system prompts to guide the model.
    """
    schema_str = json.dumps(REQUIREMENT_EXTRACTION_SCHEMA, indent=2)
    return f"""
Return ONLY valid JSON conforming to this schema. No markdown, no explanation.

Schema (JSON):
{schema_str}

Example of a valid response (do NOT repeat this; it is just an example):
{{
  "spring_type": "compression",
  "load_force_n": 500.0,
  "deflection_mm": 10.0,
  "spring_rate_n_mm": 50.0,
  "max_outer_diameter_mm": 25.0,
  "max_free_length_mm": 100.0,
  "solid_length_mm": null,
  "operating_temperature_c": 20.0,
  "corrosion_resistant": false,
  "cyclic_load": false,
  "cycles_expected": null,
  "clarification_questions": [],
  "is_complete": true
}}
"""
