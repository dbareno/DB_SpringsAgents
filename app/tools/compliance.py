"""
app/tools/compliance.py
─────────────────────────────────────────────────────────────────────────────
LangChain @tool definition for DIN/ASTM compliance verification of helical
spring designs.

Each tool is a self-contained callable decorated with @tool so LangGraph's
ToolNode can invoke it automatically when an agent emits a ToolCall.
"""

from __future__ import annotations

import json
import logging

from langchain_core.tools import tool

from app.tools.physics import (
    GOODMAN_TARGET_SF,
    GPa_TO_N_MM2,
    SHEAR_ENDURANCE_FACTOR,
    TORSIONAL_YIELD_FACTOR,
    ULTIMATE_FROM_YIELD_FACTOR,
    _shear_stress,
    _slenderness_ratio,
    _wahl_correction,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Tool 3 — Compliance Verification (DIN / ASTM)
# ─────────────────────────────────────────────────────────────────────────────


@tool
def compliance_verification_tool(
    wire_diameter_mm: float,
    mean_coil_diameter_mm: float,
    active_coils: float,
    free_length_mm: float,
    spring_rate_n_mm: float,
    load_force_n: float,
    yield_strength_mpa: float,
    shear_modulus_gpa: float,
    spring_type: str = "compression",
    max_free_length_mm: float | None = None,
    cyclic_load: bool = False,
    min_force_n: float | None = None,
    max_force_n: float | None = None,
) -> str:
    """
    Verify a proposed spring design against DIN/ASTM normative requirements.

    Checks performed
    ────────────────
    1. Wahl-corrected shear stress vs. yield (static safety factor Sf ≥ 1.3)
    2. Slenderness / buckling check  (L0/D ≤ 5.26 for fixed–free ends per DIN 2095)
    3. Spring index validity          (4 ≤ C ≤ 12 per DIN 2076 / ASTM F1276)
    4. Free-length constraint         (when max_free_length_mm is provided)
    5. Goodman fatigue criterion      (only when cyclic_load=True)

    In production: cross-references ChromaDB/Redis for additional standard clauses.

    Returns:
        JSON string with approval status, safety factors, and redesign directives.
    """
    try:
        d = wire_diameter_mm
        D = mean_coil_diameter_mm
        n_a = active_coils
        L0 = free_length_mm
        G = shear_modulus_gpa * GPa_TO_N_MM2
        Sy = yield_strength_mpa

        C = D / d
        Ks = _wahl_correction(C)
        tau = Ks * _shear_stress(load_force_n, d, D)
        safety_shear = (TORSIONAL_YIELD_FACTOR * Sy) / tau if tau > 0 else float("inf")

        # Buckling (slenderness)
        lambda_ratio = _slenderness_ratio(L0, D)
        # Per DIN 2095: critical slenderness ≤ 5.26 (fixed-free)
        CRITICAL_LAMBDA = 5.26
        safety_buckling = CRITICAL_LAMBDA / lambda_ratio if lambda_ratio > 0 else float("inf")

        # Spring index
        index_ok = 4.0 <= C <= 12.0

        failure_modes: list[str] = []
        redesign_directives: list[str] = []

        # ε = 1e-4 tolerance against floating-point rounding at the Sf=1.3 boundary.
        if safety_shear + 1e-4 < GOODMAN_TARGET_SF:
            failure_modes.append(
                f"Insufficient shear safety factor: {safety_shear:.3f} < 1.30"
            )
            redesign_directives.append(
                "Increase wire diameter d or reduce mean coil diameter D to lower shear stress."
            )

        if lambda_ratio > CRITICAL_LAMBDA:
            failure_modes.append(
                f"Buckling risk: slenderness λ = {lambda_ratio:.2f} > {CRITICAL_LAMBDA}"
            )
            redesign_directives.append(
                "Reduce free length L0 or increase mean coil diameter D to lower slenderness."
            )

        if not index_ok:
            failure_modes.append(f"Spring index C = {C:.2f} outside [4, 12]")
            redesign_directives.append(
                f"Adjust D/d ratio: current C = {C:.2f}. Target 4 ≤ C ≤ 12."
            )

        # ── Free-length constraint ─────────────────────────────────────────
        if max_free_length_mm is not None and L0 > max_free_length_mm * 1.05:
            failure_modes.append(
                f"Free length {L0:.1f} mm exceeds constraint {max_free_length_mm:.1f} mm."
            )
            redesign_directives.append(
                "Reduce free length by using fewer active coils or a smaller wire "
                "diameter. Compensate with larger D if needed to maintain spring rate."
            )

        # ── Fatigue (Goodman) ──────────────────────────────────────────────
        safety_fatigue: float | None = None
        if cyclic_load and min_force_n is not None and max_force_n is not None:
            F_mean = (max_force_n + min_force_n) / 2.0
            F_alt = (max_force_n - min_force_n) / 2.0
            tau_mean = Ks * _shear_stress(F_mean, d, D)
            tau_alt = Ks * _shear_stress(F_alt, d, D)
            # Approximation: Ses ≈ 0.324 * Sut (Zimmerli for steel wire)
            # Sut ≈ 1.25 * Sy for typical spring steel
            Sut_approx = ULTIMATE_FROM_YIELD_FACTOR * Sy
            Ses = SHEAR_ENDURANCE_FACTOR * Sut_approx  # endurance limit in shear
            Ssy = TORSIONAL_YIELD_FACTOR * Sy          # torsional yield limit
            # Goodman: (tau_alt/Ses) + (tau_mean/Ssy) = 1 at failure
            goodman_lhs = (tau_alt / Ses) + (tau_mean / Ssy)
            safety_fatigue = 1.0 / goodman_lhs if goodman_lhs > 0 else float("inf")
            if safety_fatigue < GOODMAN_TARGET_SF:
                failure_modes.append(
                    f"Fatigue failure risk (Goodman Sf = {safety_fatigue:.3f} < 1.30)"
                )
                redesign_directives.append(
                    "Increase wire diameter or reduce operating stress range. "
                    "Consider shot-peened surface treatment or higher-grade alloy."
                )

        # Determine applicable standard
        std_map = {
            "compression": "DIN 2095 / ASTM A125",
            "extension": "DIN 2097 / ASTM A125",
            "torsion": "DIN 2194 / ASTM F1123",
        }
        applicable_standard = std_map.get(spring_type, "DIN 2095 / ASTM A125")

        approved = len(failure_modes) == 0

        report = {
            "approved": approved,
            "safety_factor_shear": round(safety_shear, 3),
            "safety_factor_buckling": round(safety_buckling, 3),
            "safety_factor_fatigue": (
                round(safety_fatigue, 3) if safety_fatigue is not None else None
            ),
            "spring_index": round(C, 3),
            "wahl_factor": round(Ks, 4),
            "corrected_shear_stress_mpa": round(tau, 3),
            "slenderness_ratio": round(lambda_ratio, 3),
            "applicable_standard": applicable_standard,
            "failure_modes": failure_modes,
            "redesign_directives": redesign_directives,
        }
        return json.dumps({"status": "ok", "report": report})

    except Exception as exc:
        logger.exception("compliance_verification_tool failed")
        return json.dumps({"status": "error", "message": str(exc)})
