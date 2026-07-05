"""
app/tools/compliance.py
─────────────────────────────────────────────────────────────────────────────
LangChain @tool definition for DIN/ASTM compliance verification of helical
spring designs.

Each tool is a self-contained callable decorated with @tool so LangGraph's
ToolNode can invoke it automatically when an agent emits a ToolCall.

Strategy pattern (Phase 4 — Engineering Depth)
───────────────────────────────────────────────
``compliance_verification_tool`` is a thin dispatcher: it routes to a
``ComplianceEngine`` implementation selected by ``spring_type`` and delegates
the actual normative checks to it. ``CompressionComplianceEngine`` is the
byte-identical reference implementation of the pre-Phase-4 logic (shear +
buckling + spring index + free length + Goodman fatigue). The extension and
torsion engines reuse the same ``ComplianceReport`` JSON shape but check
hook stress / arm-bending + coil-shear instead, and never apply the
buckling check (both spring types are effectively constrained differently
than a free-standing compression coil, so ``safety_factor_buckling`` is
reported as ``None`` for them — callers treat that as "not applicable").
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import Any

from langchain_core.tools import tool

from app.tools.physics import (
    GOODMAN_TARGET_SF,
    SHEAR_ENDURANCE_FACTOR,
    TORSIONAL_YIELD_FACTOR,
    ULTIMATE_FROM_YIELD_FACTOR,
    _arm_bending_stress,
    _bending_correction,
    _coil_torsion_stress,
    _hook_bending_stress,
    _hook_torsion_stress,
    _shear_stress,
    _slenderness_ratio,
    _torsion_correction,
    _wahl_correction,
)

logger = logging.getLogger(__name__)


_STANDARD_MAP = {
    "compression": "DIN 2095 / ASTM A125",
    "extension": "DIN 2097 / ASTM A125",
    "torsion": "DIN 2194 / ASTM F1123",
}


# ─────────────────────────────────────────────────────────────────────────────
# Strategy interface — ComplianceEngine
# ─────────────────────────────────────────────────────────────────────────────


class ComplianceEngine(ABC):
    """
    Strategy interface for per-spring-type compliance verification.

    Every engine returns a report dict with the SAME keys as
    ``ComplianceReport`` (approved, safety_factor_shear,
    safety_factor_buckling, applicable_standard, failure_modes,
    redesign_directives, plus the shared informational fields) — only the
    checks that populate ``failure_modes``/``redesign_directives`` differ.
    """

    name: str = "base"

    @abstractmethod
    def check(self, **kwargs: Any) -> dict[str, Any]:
        """Run this engine's normative checks and return the report dict."""
        raise NotImplementedError


# ─────────────────────────────────────────────────────────────────────────────
# CompressionComplianceEngine — reference implementation (unchanged behavior)
# ─────────────────────────────────────────────────────────────────────────────


class CompressionComplianceEngine(ComplianceEngine):
    """
    Compression spring compliance — the original (pre-Phase-4) checks, moved
    here verbatim: Wahl-corrected shear stress, slenderness/buckling, spring
    index, free-length constraint, and Goodman fatigue.
    """

    name = "compression"
    CRITICAL_LAMBDA = 5.26

    def check(self, **kwargs: Any) -> dict[str, Any]:
        d = kwargs["wire_diameter_mm"]
        D = kwargs["mean_coil_diameter_mm"]
        L0 = kwargs["free_length_mm"]
        load_force_n = kwargs["load_force_n"]
        Sy = kwargs["yield_strength_mpa"]
        max_free_length_mm = kwargs.get("max_free_length_mm")
        cyclic_load = kwargs.get("cyclic_load", False)
        min_force_n = kwargs.get("min_force_n")
        max_force_n = kwargs.get("max_force_n")

        C = D / d
        Ks = _wahl_correction(C)
        tau = Ks * _shear_stress(load_force_n, d, D)
        safety_shear = (TORSIONAL_YIELD_FACTOR * Sy) / tau if tau > 0 else float("inf")

        lambda_ratio = _slenderness_ratio(L0, D)
        safety_buckling = (
            self.CRITICAL_LAMBDA / lambda_ratio if lambda_ratio > 0 else float("inf")
        )

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

        if lambda_ratio > self.CRITICAL_LAMBDA:
            failure_modes.append(
                f"Buckling risk: slenderness λ = {lambda_ratio:.2f} > {self.CRITICAL_LAMBDA}"
            )
            redesign_directives.append(
                "Reduce free length L0 or increase mean coil diameter D to lower slenderness."
            )

        if not index_ok:
            failure_modes.append(f"Spring index C = {C:.2f} outside [4, 12]")
            redesign_directives.append(
                f"Adjust D/d ratio: current C = {C:.2f}. Target 4 ≤ C ≤ 12."
            )

        if max_free_length_mm is not None and L0 > max_free_length_mm * 1.05:
            failure_modes.append(
                f"Free length {L0:.1f} mm exceeds constraint {max_free_length_mm:.1f} mm."
            )
            redesign_directives.append(
                "Reduce free length by using fewer active coils or a smaller wire "
                "diameter. Compensate with larger D if needed to maintain spring rate."
            )

        safety_fatigue: float | None = None
        if cyclic_load and min_force_n is not None and max_force_n is not None:
            F_mean = (max_force_n + min_force_n) / 2.0
            F_alt = (max_force_n - min_force_n) / 2.0
            tau_mean = Ks * _shear_stress(F_mean, d, D)
            tau_alt = Ks * _shear_stress(F_alt, d, D)
            Sut_approx = ULTIMATE_FROM_YIELD_FACTOR * Sy
            Ses = SHEAR_ENDURANCE_FACTOR * Sut_approx
            Ssy = TORSIONAL_YIELD_FACTOR * Sy
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

        approved = len(failure_modes) == 0

        return {
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
            "failure_modes": failure_modes,
            "redesign_directives": redesign_directives,
        }


# ─────────────────────────────────────────────────────────────────────────────
# ExtensionComplianceEngine — hook stress checks, no buckling
# ─────────────────────────────────────────────────────────────────────────────


class ExtensionComplianceEngine(ComplianceEngine):
    """
    Extension spring compliance. Primary check: hook bending stress at
    point A (Sf ≥ 1.3 against yield). Secondary: coil body shear (Wahl-
    corrected, same as compression) and hook attachment/torsion shear.
    Buckling does not apply — extension springs are not free-standing
    columns under axial compression, so ``safety_factor_buckling`` is
    reported as ``1.0`` (N/A sentinel).

    ``hook_bending_stress_mpa``/``hook_torsion_stress_mpa`` may be passed in
    directly (e.g. from ``ExtensionEngine.compute_geometry()``'s richer
    output); when absent — as is the case when this tool is invoked from
    Agent 4 with only the shared ``SpringGeometry`` fields — the hook
    stresses are RE-DERIVED here from ``d``/``D``/``load_force_n`` using the
    same hook-geometry approximation (``C1 ≈ C``, ``C2 ≈ 2.0``) as the
    geometry engine, so the check is never silently skipped.
    """

    name = "extension"

    def check(self, **kwargs: Any) -> dict[str, Any]:
        d = kwargs["wire_diameter_mm"]
        D = kwargs["mean_coil_diameter_mm"]
        load_force_n = kwargs["load_force_n"]
        Sy = kwargs["yield_strength_mpa"]
        hook_bending_stress_mpa = kwargs.get("hook_bending_stress_mpa")
        hook_torsion_stress_mpa = kwargs.get("hook_torsion_stress_mpa")

        C = D / d
        Ks = _wahl_correction(C)
        tau_body = Ks * _shear_stress(load_force_n, d, D)
        safety_body_shear = (
            (TORSIONAL_YIELD_FACTOR * Sy) / tau_body if tau_body > 0 else float("inf")
        )

        if hook_bending_stress_mpa is None:
            Kb = _bending_correction(C)
            hook_bending_stress_mpa = _hook_bending_stress(load_force_n, d, D, Kb)
        sigma_hook = hook_bending_stress_mpa
        safety_hook = Sy / sigma_hook if sigma_hook > 0 else float("inf")

        if hook_torsion_stress_mpa is None:
            Kw = _torsion_correction(2.0)
            hook_torsion_stress_mpa = _hook_torsion_stress(load_force_n, d, D, Kw)
        tau_hook = hook_torsion_stress_mpa
        safety_hook_torsion = (
            (TORSIONAL_YIELD_FACTOR * Sy) / tau_hook if tau_hook > 0 else float("inf")
        )

        # The hook (bending) is the governing/primary failure mode for
        # extension springs — report the minimum of the three as the
        # semantic "safety_factor_shear" per the shared ComplianceReport shape.
        safety_shear = min(safety_hook, safety_body_shear, safety_hook_torsion)

        index_ok = 3.0 <= C <= 10.0

        failure_modes: list[str] = []
        redesign_directives: list[str] = []

        if safety_hook + 1e-4 < GOODMAN_TARGET_SF:
            failure_modes.append(
                f"Insufficient hook bending safety factor: {safety_hook:.3f} < 1.30"
            )
            redesign_directives.append(
                "Increase wire diameter or reduce mean coil diameter to lower hook "
                "bending stress at point A."
            )

        if safety_body_shear + 1e-4 < GOODMAN_TARGET_SF:
            failure_modes.append(
                f"Insufficient coil body shear safety factor: {safety_body_shear:.3f} < 1.30"
            )
            redesign_directives.append(
                "Increase wire diameter d or reduce mean coil diameter D to lower "
                "coil body shear stress."
            )

        if safety_hook_torsion + 1e-4 < GOODMAN_TARGET_SF:
            failure_modes.append(
                f"Insufficient hook torsion safety factor: {safety_hook_torsion:.3f} < 1.30"
            )
            redesign_directives.append(
                "Increase the hook bend radius or wire diameter to lower torsional "
                "shear stress at point B."
            )

        if not index_ok:
            failure_modes.append(f"Spring index C = {C:.2f} outside [3, 10]")
            redesign_directives.append(
                f"Adjust D/d ratio: current C = {C:.2f}. Target 3 ≤ C ≤ 10."
            )

        approved = len(failure_modes) == 0

        return {
            "approved": approved,
            "safety_factor_shear": round(safety_shear, 3),
            "safety_factor_buckling": 1.0,  # N/A for this spring type — buckling
            # check doesn't apply (extension/torsion springs aren't free-standing
            # axial columns); reported as 1.0 (neutral) since ComplianceReport's
            # safety_factor_buckling is a required float field.
            "safety_factor_fatigue": None,
            "spring_index": round(C, 3),
            "wahl_factor": round(Ks, 4),
            "corrected_shear_stress_mpa": round(tau_body, 3),
            "slenderness_ratio": None,
            "failure_modes": failure_modes,
            "redesign_directives": redesign_directives,
        }


# ─────────────────────────────────────────────────────────────────────────────
# TorsionComplianceEngine — arm bending + coil shear checks, no buckling
# ─────────────────────────────────────────────────────────────────────────────


class TorsionComplianceEngine(ComplianceEngine):
    """
    Torsion spring compliance. Primary check: arm bending stress at the
    coil-arm junction (Sf ≥ 1.3 against yield). Secondary: coil torsional
    shear from the applied moment. Buckling does not apply — torsion
    springs are constrained at both ends via the arms/legs, not a
    free-standing axial column, so ``safety_factor_buckling`` is reported
    as ``1.0`` (N/A sentinel).

    ``arm_bending_stress_mpa``/``coil_torsion_stress_mpa`` may be passed in
    directly (e.g. from ``TorsionEngine.compute_geometry()``'s richer
    output); when absent — as is the case when this tool is invoked from
    Agent 4 with only the shared ``SpringGeometry`` fields — both stresses
    are RE-DERIVED here from ``d``/``D`` and the applied moment (taken from
    ``torsion_moment_n_mm`` if supplied, else ``load_force_n * D/2`` as the
    same moment-arm approximation the geometry engine uses when no explicit
    arm length is known), so the check is never silently skipped.
    """

    name = "torsion"

    def check(self, **kwargs: Any) -> dict[str, Any]:
        d = kwargs["wire_diameter_mm"]
        D = kwargs["mean_coil_diameter_mm"]
        Sy = kwargs["yield_strength_mpa"]
        load_force_n = kwargs.get("load_force_n", 0.0)
        arm_bending_stress_mpa = kwargs.get("arm_bending_stress_mpa")
        coil_torsion_stress_mpa = kwargs.get("coil_torsion_stress_mpa")
        torsion_moment_n_mm = kwargs.get("torsion_moment_n_mm")

        C = D / d
        M = torsion_moment_n_mm if torsion_moment_n_mm is not None else load_force_n * (D / 2.0)

        if arm_bending_stress_mpa is None:
            Kb = _bending_correction(C)
            arm_bending_stress_mpa = _arm_bending_stress(M, d, Kb)
        sigma_arm = arm_bending_stress_mpa
        safety_arm = Sy / sigma_arm if sigma_arm > 0 else float("inf")

        if coil_torsion_stress_mpa is None:
            coil_torsion_stress_mpa = _coil_torsion_stress(M, d)
        tau_coil = coil_torsion_stress_mpa
        safety_coil = (
            (TORSIONAL_YIELD_FACTOR * Sy) / tau_coil if tau_coil > 0 else float("inf")
        )

        # Arm bending is the governing/primary failure mode for torsion
        # springs — report the minimum of the two as the semantic
        # "safety_factor_shear" per the shared ComplianceReport shape.
        safety_shear = min(safety_arm, safety_coil)

        index_ok = 3.0 <= C <= 10.0

        failure_modes: list[str] = []
        redesign_directives: list[str] = []

        if safety_arm + 1e-4 < GOODMAN_TARGET_SF:
            failure_modes.append(
                f"Insufficient arm bending safety factor: {safety_arm:.3f} < 1.30"
            )
            redesign_directives.append(
                "Increase wire diameter or reduce the moment-arm length to lower "
                "bending stress at the coil-arm junction."
            )

        if safety_coil + 1e-4 < GOODMAN_TARGET_SF:
            failure_modes.append(
                f"Insufficient coil torsion safety factor: {safety_coil:.3f} < 1.30"
            )
            redesign_directives.append(
                "Increase wire diameter d to lower torsional shear stress in the coil body."
            )

        if not index_ok:
            failure_modes.append(f"Spring index C = {C:.2f} outside [3, 10]")
            redesign_directives.append(
                f"Adjust D/d ratio: current C = {C:.2f}. Target 3 ≤ C ≤ 10."
            )

        approved = len(failure_modes) == 0

        return {
            "approved": approved,
            "safety_factor_shear": round(safety_shear, 3),
            "safety_factor_buckling": 1.0,  # N/A for this spring type — buckling
            # check doesn't apply (extension/torsion springs aren't free-standing
            # axial columns); reported as 1.0 (neutral) since ComplianceReport's
            # safety_factor_buckling is a required float field.
            "safety_factor_fatigue": None,
            "spring_index": round(C, 3),
            "wahl_factor": None,
            "corrected_shear_stress_mpa": None,
            "slenderness_ratio": None,
            "failure_modes": failure_modes,
            "redesign_directives": redesign_directives,
        }


_COMPLIANCE_ENGINES: dict[str, ComplianceEngine] = {
    "compression": CompressionComplianceEngine(),
    "extension": ExtensionComplianceEngine(),
    "torsion": TorsionComplianceEngine(),
}


def _get_compliance_engine(spring_type: str) -> ComplianceEngine:
    """Route to the compliance engine for ``spring_type``, default compression."""
    return _COMPLIANCE_ENGINES.get(spring_type, _COMPLIANCE_ENGINES["compression"])


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
    hook_bending_stress_mpa: float | None = None,
    hook_torsion_stress_mpa: float | None = None,
    arm_bending_stress_mpa: float | None = None,
    coil_torsion_stress_mpa: float | None = None,
    torsion_moment_n_mm: float | None = None,
) -> str:
    """
    Verify a proposed spring design against DIN/ASTM normative requirements.

    Dispatches to a per-``spring_type`` ``ComplianceEngine`` (compression,
    extension, or torsion). Unknown spring types fall back to the
    compression engine to preserve legacy behavior.

    Checks performed
    ────────────────
    Compression:
      1. Wahl-corrected shear stress vs. yield (static safety factor Sf ≥ 1.3)
      2. Slenderness / buckling check  (L0/D ≤ 5.26 for fixed–free ends per DIN 2095)
      3. Spring index validity          (4 ≤ C ≤ 12 per DIN 2076 / ASTM F1276)
      4. Free-length constraint         (when max_free_length_mm is provided)
      5. Goodman fatigue criterion      (only when cyclic_load=True)
    Extension:
      1. Hook bending stress at point A vs. yield (Sf ≥ 1.3)
      2. Coil body shear (Wahl-corrected, same formula as compression)
      3. Hook torsional shear at point B
      4. Spring index validity (3 ≤ C ≤ 10)
    Torsion:
      1. Arm bending stress at the coil-arm junction vs. yield (Sf ≥ 1.3)
      2. Coil torsional shear from the applied moment
      3. Spring index validity (3 ≤ C ≤ 10)

    In production: cross-references ChromaDB/Redis for additional standard clauses.

    Returns:
        JSON string with approval status, safety factors, and redesign directives.
    """
    try:
        engine = _get_compliance_engine(spring_type)
        report = engine.check(
            wire_diameter_mm=wire_diameter_mm,
            mean_coil_diameter_mm=mean_coil_diameter_mm,
            active_coils=active_coils,
            free_length_mm=free_length_mm,
            spring_rate_n_mm=spring_rate_n_mm,
            load_force_n=load_force_n,
            yield_strength_mpa=yield_strength_mpa,
            shear_modulus_gpa=shear_modulus_gpa,
            max_free_length_mm=max_free_length_mm,
            cyclic_load=cyclic_load,
            min_force_n=min_force_n,
            max_force_n=max_force_n,
            hook_bending_stress_mpa=hook_bending_stress_mpa,
            hook_torsion_stress_mpa=hook_torsion_stress_mpa,
            arm_bending_stress_mpa=arm_bending_stress_mpa,
            coil_torsion_stress_mpa=coil_torsion_stress_mpa,
            torsion_moment_n_mm=torsion_moment_n_mm,
        )
        report["applicable_standard"] = _STANDARD_MAP.get(
            spring_type, "DIN 2095 / ASTM A125"
        )
        return json.dumps({"status": "ok", "report": report})

    except Exception as exc:
        logger.exception("compliance_verification_tool failed")
        return json.dumps({"status": "error", "message": str(exc)})
