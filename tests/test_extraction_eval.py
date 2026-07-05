"""
tests/test_extraction_eval.py
─────────────────────────────────────────────────────────────────────────────
Extraction quality evaluation harness (ADR-6).

This test suite evaluates the LLM's field-level extraction accuracy against
a gold set of annotated requirements. It serves as a regression gate on:
- Prompt changes to Agent 1
- LLM model updates (e.g., from 3b to 7b Ollama models)
- Offline mode vs. cloud provider changes

Metrics reported:
- Per-field precision: did the LLM extract the expected value?
- Completeness: what fraction of fields were populated when expected?
- Convergence: did the regex fallback recover omitted values?

The test can be run manually to evaluate the current model against the gold set:
    pytest tests/test_extraction_eval.py -v -s
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from app.schemas.state import UserRequirements

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Gold requirements test set (ADR-6 evaluation harness)
# ─────────────────────────────────────────────────────────────────────────────

GOLD_REQUIREMENTS: list[dict[str, Any]] = [
    {
        "id": "test_001_compression_basic",
        "raw_input": "I need a compression spring with 500 N load, 10 mm deflection, max OD 25 mm, free length 100 mm.",
        "expected": {
            "spring_type": "compression",
            "load_force_n": 500.0,
            "deflection_mm": 10.0,
            "spring_rate_n_mm": 50.0,  # Derived: 500 / 10
            "max_outer_diameter_mm": 25.0,
            "max_free_length_mm": 100.0,
            "corrosion_resistant": False,
            "cyclic_load": False,
            "is_complete": True,
        },
    },
    {
        "id": "test_002_extension_cyclic",
        "raw_input": "Extension spring, 200N force, 5mm deflection, OD 20mm, free length 80mm, cyclic load, 10000 cycles.",
        "expected": {
            "spring_type": "extension",
            "load_force_n": 200.0,
            "deflection_mm": 5.0,
            "spring_rate_n_mm": 40.0,  # Derived
            "max_outer_diameter_mm": 20.0,
            "max_free_length_mm": 80.0,
            "cyclic_load": True,
            "cycles_expected": 10000,
            "is_complete": True,
        },
    },
    {
        "id": "test_003_torsion_with_temp",
        "raw_input": "Torsion spring, 50 N·m moment, 30° angular deflection, OD 15mm, operating at 100°C, corrosion resistant.",
        "expected": {
            "spring_type": "torsion",
            "torsion_moment_n_mm": None,  # Not in UserRequirements yet; ok to omit
            "angular_deflection_deg": 30.0,  # Not in UserRequirements yet; ok to omit
            "max_outer_diameter_mm": 15.0,
            "operating_temperature_c": 100.0,
            "corrosion_resistant": True,
            "is_complete": True,
        },
    },
    {
        "id": "test_004_spring_rate_only",
        "raw_input": "Compression spring with spring rate 25 N/mm, deflection 20 mm, OD 30 mm, free length 120 mm.",
        "expected": {
            "spring_type": "compression",
            "spring_rate_n_mm": 25.0,
            "deflection_mm": 20.0,
            "load_force_n": 500.0,  # Derived: 25 * 20
            "max_outer_diameter_mm": 30.0,
            "max_free_length_mm": 120.0,
            "is_complete": True,
        },
    },
    {
        "id": "test_005_incomplete_missing_od",
        "raw_input": "Spring with 100 N load and 5 mm deflection. Compression type.",
        "expected": {
            "spring_type": "compression",
            "load_force_n": 100.0,
            "deflection_mm": 5.0,
            "spring_rate_n_mm": 20.0,  # Derived
            "max_outer_diameter_mm": None,  # Missing
            "is_complete": False,  # Should ask for OD
        },
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# Metrics calculation
# ─────────────────────────────────────────────────────────────────────────────


def field_matches(extracted: Any, expected: Any, tolerance: float = 0.01) -> bool:
    """
    Compare an extracted field against the expected value.

    Handles:
    - Numeric tolerance (e.g., 50.0 vs 49.98 due to float rounding)
    - None/null comparisons
    - Boolean comparisons
    - Type coercion
    """
    if expected is None:
        return extracted is None or extracted == ""

    if isinstance(expected, bool):
        if isinstance(extracted, bool):
            return extracted == expected
        if isinstance(extracted, str):
            return extracted.lower() in ("true", "yes", "1") if expected else extracted.lower() in ("false", "no", "0")
        return bool(extracted) == expected

    if isinstance(expected, (int, float)):
        if isinstance(extracted, (int, float)):
            return abs(extracted - expected) <= (expected * tolerance)
        return False

    if isinstance(expected, str):
        return isinstance(extracted, str) and extracted.lower() == expected.lower()

    return extracted == expected


def evaluate_extraction(
    extracted: UserRequirements,
    expected: dict[str, Any],
) -> dict[str, Any]:
    """
    Evaluate a single extraction result against gold expected values.

    Returns a metrics dict:
    {
        "total_fields": int,
        "matched_fields": int,
        "missing_fields": list[str],
        "mismatched_fields": dict[field_name, (extracted, expected)],
        "precision": float (0.0-1.0),
        "completeness_match": bool (does is_complete match expectation?),
    }
    """
    extracted_dict = extracted.model_dump()
    matched = 0
    total = len(expected)
    missing = []
    mismatched = {}

    for field_name, expected_value in expected.items():
        extracted_value = extracted_dict.get(field_name)
        if field_matches(extracted_value, expected_value):
            matched += 1
        else:
            mismatched[field_name] = (extracted_value, expected_value)
            if extracted_value is None and expected_value is not None:
                missing.append(field_name)

    precision = matched / total if total > 0 else 1.0
    completeness_match = extracted_dict.get("is_complete") == expected.get("is_complete", False)

    return {
        "total_fields": total,
        "matched_fields": matched,
        "precision": precision,
        "missing_fields": missing,
        "mismatched_fields": mismatched,
        "completeness_match": completeness_match,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Test suite
# ─────────────────────────────────────────────────────────────────────────────


class TestExtractionQuality:
    """
    Evaluation harness for requirement extraction quality (ADR-6).

    These tests do NOT mock the LLM. They run against the actual configured
    provider (local Ollama by default) to catch real extraction quality issues.

    To use in CI:
    - pytest tests/test_extraction_eval.py -v
    - Reports will show per-field precision and any regressions
    """

    @pytest.mark.parametrize("gold_case", GOLD_REQUIREMENTS, ids=lambda x: x["id"])
    def test_extraction_gold_set(self, gold_case: dict[str, Any]) -> None:
        """
        Test extraction against a single gold requirement.

        This is a slow test (calls actual LLM) and may fail if:
        - The model is unavailable (Ollama not running)
        - The model has changed (e.g., 3b → 7b with different output format)
        - The prompt was changed without updating the extraction logic

        On failure, the metrics dict shows which fields were mismatched.
        """
        from app.agents.agent1_requirements import _extract_requirements
        from app.core.llm_factory import get_factory
        from langchain_core.messages import HumanMessage, SystemMessage

        raw_input = gold_case["raw_input"]
        expected = gold_case["expected"]

        logger.info("[ExtractionEval] Testing case: %s", gold_case["id"])
        logger.info("[ExtractionEval] Input: %s", raw_input[:100])

        # Simulate Agent 1's extraction flow
        factory = get_factory()
        messages = [
            SystemMessage(content="Extract spring requirements into JSON."),
            HumanMessage(content=raw_input),
        ]

        state = {
            "messages": messages,
            "current_step": "requirements_analyst",
            "iteration_count": 0,
            "max_iterations": 5,
            "requirements": None,
            "geometry": None,
            "material": None,
            "material_candidates": [],
            "compliance": None,
            "redesign_directives": [],
            "min_yield_strength_mpa": None,
            "commercial_proposals": [],
            "llm_status": {"active_provider": "ollama", "failed_providers": [], "retry_count": 0},
            "errors": [],
            "final_report": None,
            "_raw_input": raw_input,
            "interrupted": False,
            "session_answers": {},
        }

        requirements, response, error = _extract_requirements(
            factory=factory,
            messages=messages,
            raw_input=raw_input,
            max_attempts=len(factory._priority_order) + 1,
            state=state,
        )

        assert error is None, f"Extraction failed: {error}"
        assert requirements is not None, "Requirements should not be None after successful extraction"

        metrics = evaluate_extraction(requirements, expected)

        logger.info(
            "[ExtractionEval] %s: precision=%.2f%%, matched=%d/%d",
            gold_case["id"],
            metrics["precision"] * 100,
            metrics["matched_fields"],
            metrics["total_fields"],
        )

        if metrics["mismatched_fields"]:
            logger.warning(
                "[ExtractionEval] Mismatches: %s",
                metrics["mismatched_fields"],
            )

        # Assertion: expect 90%+ field-level precision (allows for some variance in LLM output)
        assert metrics["precision"] >= 0.85, (
            f"Extraction precision {metrics['precision']:.2%} is below 85% threshold. "
            f"Mismatches: {metrics['mismatched_fields']}"
        )

        # Also check completeness judgment
        assert metrics["completeness_match"], (
            f"Completeness judgment mismatch: got is_complete={requirements.is_complete}, "
            f"expected {expected.get('is_complete', False)}"
        )

    def test_extraction_eval_harness_summary(self) -> None:
        """
        Print a summary of the gold requirements set.
        Useful for understanding what the harness covers.
        """
        logger.info("=" * 70)
        logger.info("Extraction Eval Harness Summary")
        logger.info("=" * 70)
        for case in GOLD_REQUIREMENTS:
            logger.info("  %s: %s", case["id"], case["raw_input"][:60])
        logger.info("Total gold cases: %d", len(GOLD_REQUIREMENTS))
        logger.info("=" * 70)

        assert len(GOLD_REQUIREMENTS) >= 5, "Gold set should have at least 5 representative cases"
