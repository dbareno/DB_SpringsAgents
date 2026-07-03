"""
app/tools/spring_tools.py
─────────────────────────────────────────────────────────────────────────────
LangChain @tool definitions for the Spring Design Agent system.

Each tool is a self-contained callable decorated with @tool so LangGraph's
ToolNode can invoke it automatically when an agent emits a ToolCall.

Mathematical conventions (SI-mixed units, industry standard)
─────────────────────────────────────────────────────────────
    d   → wire diameter         [mm]
    D   → mean coil diameter    [mm]
    C   → spring index = D/d    [dimensionless]
    n_a → active coils          [dimensionless]
    L0  → free length           [mm]
    p   → pitch                 [mm]
    k   → spring rate           [N/mm]
    G   → shear modulus         [GPa → converted to N/mm²]
    Ks  → Wahl correction factor [dimensionless]
    τ   → shear stress          [MPa]
    Sy  → yield strength        [MPa]
"""

from __future__ import annotations

import json
import logging
import math
from typing import Any

import numpy as np
import pandas as pd
from langchain_core.tools import tool
from scipy.optimize import OptimizeResult, minimize  # type: ignore[import]

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

GPa_TO_N_MM2 = 1_000.0  # 1 GPa = 1 000 N/mm²
MPa_TO_N_MM2 = 1.0       # 1 MPa = 1 N/mm²


def _wahl_correction(C: float) -> float:
    """Wahl stress-correction factor Ks = (4C-1)/(4C-4) + 0.615/C."""
    return (4 * C - 1) / (4 * C - 4) + 0.615 / C


def _spring_rate(d: float, D: float, n_a: float, G_n_mm2: float) -> float:
    """Helical spring rate k = G*d⁴ / (8*D³*n_a)  [N/mm]."""
    return (G_n_mm2 * d**4) / (8.0 * D**3 * n_a)


def _shear_stress(F: float, d: float, D: float) -> float:
    """Uncorrected shear stress τ = 8FD / (πd³)  [N/mm² = MPa]."""
    return (8.0 * F * D) / (math.pi * d**3)


def _slenderness_ratio(L0: float, D: float) -> float:
    """Slenderness ratio λ = L0/D (pandeo check)."""
    return L0 / D


# ─────────────────────────────────────────────────────────────────────────────
# Tool 1 — Spring Geometry Calculator
# ─────────────────────────────────────────────────────────────────────────────


@tool
def calculate_spring_geometry_tool(
    spring_type: str,
    load_force_n: float,
    deflection_mm: float,
    max_outer_diameter_mm: float | None = None,
    max_free_length_mm: float | None = None,
    shear_modulus_gpa: float = 79.3,
    yield_strength_mpa: float = 1_500.0,
    dead_coils: float = 2.0,
) -> str:
    """
    Compute optimal helical spring geometry using scipy.optimize.minimize.

    This tool solves for the best combination of wire diameter (d), mean coil
    diameter (D), and active coil count (n_a) that satisfies the required
    spring rate while minimising total wire volume (∝ material cost).

    Args:
        spring_type:          One of 'compression', 'extension', 'torsion'.
        load_force_n:         Required operating load in Newtons.
        deflection_mm:        Required deflection at that load in mm.
        max_outer_diameter_mm: Hard OD constraint (mm). None = unconstrained.
        max_free_length_mm:   Hard free-length constraint (mm). None = unconstrained.
        shear_modulus_gpa:    G of the chosen material in GPa (default: 79.3 = steel).
        yield_strength_mpa:   Sy of the material in MPa (default: 1500).
        dead_coils:           Non-active (dead) coils at each end (default: 2.0 total).

    Returns:
        JSON string with the computed geometry dict or an error message.
    """
    try:
        G = shear_modulus_gpa * GPa_TO_N_MM2          # → N/mm²
        k_target = load_force_n / deflection_mm        # required spring rate N/mm

        # ── Objective: minimise total wire volume V = π²/4 * d² * D * n_t ─
        def objective(x: np.ndarray) -> float:
            d, D, n_a = x
            n_t = n_a + dead_coils
            return (math.pi**2 / 4.0) * d**2 * D * n_t

        # ── Constraint helpers ──────────────────────────────────────────────
        # Standard requires Sf ≥ 1.3 for shear (DIN 2095 / EN 13906-1)
        TARGET_SF = 1.3
        ALLOWABLE_SHEAR_MPA = 0.45 * yield_strength_mpa / TARGET_SF

        # ── Constraints ────────────────────────────────────────────────────
        constraints = [
            # Spring rate must match target within ±1%
            {
                "type": "eq",
                "fun": lambda x: _spring_rate(x[0], x[1], x[2], G) - k_target,
            },
            # Shear stress safety: Ks*τ ≤ allowable (DIN: Sf ≥ 1.3)
            {
                "type": "ineq",
                "fun": lambda x: (
                    ALLOWABLE_SHEAR_MPA
                    - _wahl_correction(x[1] / x[0])
                    * _shear_stress(load_force_n, x[0], x[1])
                ),
            },
            # Spring index bounds: 4 ≤ C ≤ 12
            {"type": "ineq", "fun": lambda x: x[1] / x[0] - 4.0},
            {"type": "ineq", "fun": lambda x: 12.0 - x[1] / x[0]},
        ]

        bounds_list = [
            (0.5, 20.0),   # d  [mm]
            (5.0, 200.0),  # D  [mm]
            (2.0, 60.0),   # n_a
        ]

        # Add OD constraint if provided
        if max_outer_diameter_mm is not None:
            constraints.append(
                {
                    "type": "ineq",
                    # OD = D + d ≤ max_outer_diameter_mm
                    "fun": lambda x, od=max_outer_diameter_mm: od - (x[1] + x[0]),
                }
            )
            # Tighten D upper bound
            bounds_list[1] = (5.0, max_outer_diameter_mm - 0.5)

        # Free-length constraint: L0 = (n_a + dead_coils)*d + deflection + 2*d
        if max_free_length_mm is not None:
            _, dead = dead_coils, 2.0  # dead coils default
            constraints.append({
                "type": "ineq",
                "fun": lambda x, mfl=max_free_length_mm, dfl=deflection_mm:
                    mfl - ((x[2] + 4.0) * x[0] + dfl),
            })

        # ── Analytical initial guess (includes Wahl factor Ks) ─────────────
        # The old fallback formula d = (8F/(π·allowable))^(1/3) ignored the
        # Wahl correction Ks, producing d that was always too small → Sf ≈ 0.22.
        #
        # Corrected approach:  τ = Ks·8·F·D/(π·d³) ;  with D = C·d:
        #     τ = Ks·8·F·C/(π·d²)  →  d = sqrt(Ks·8·F·C / (π·allowable))
        #
        # We target C = 8 (mid-range spring index), compute Ks(C=8) ≈ 1.184.
        _C_guess = 8.0
        _Ks_guess = (4 * _C_guess - 1) / (4 * _C_guess - 4) + 0.615 / _C_guess
        # Use a 3 % margin below the absolute allowable so that floating-point
        # rounding never produces a geometry that JUST misses Sf ≥ 1.3.
        _analytical_allowable = ALLOWABLE_SHEAR_MPA * 0.97
        d0 = math.sqrt(
            _Ks_guess * 8.0 * load_force_n * _C_guess
            / (math.pi * _analytical_allowable)
        )
        D0 = _C_guess * d0
        n0 = G * d0 ** 4 / (8.0 * D0 ** 3 * k_target)
        x0 = np.array([d0, D0, n0])
        logger.debug(
            "[SpringTool] Analytical guess: d=%.3f, D=%.3f, n_a=%.1f (Ks=%.4f)",
            d0, D0, n0, _Ks_guess,
        )

        result: OptimizeResult = minimize(
            objective,
            x0,
            method="SLSQP",
            bounds=bounds_list,
            constraints=constraints,
            options={"ftol": 1e-9, "maxiter": 1_000},
        )

        if not result.success:
            logger.warning(
                "Optimizer did not converge: %s — using analytical estimate.",
                result.message,
            )
            result.x = np.array([d0, D0, n0])

        d, D, n_a = result.x
        n_t = n_a + dead_coils
        C = D / d
        Ks = _wahl_correction(C)
        k_actual = _spring_rate(d, D, n_a, G)
        L0_solid = n_t * d                    # solid length
        pitch = (deflection_mm / n_a) + d     # operating pitch
        L0 = L0_solid + deflection_mm + 2 * d  # rough free length

        # Torsion spring adjustments (angular geometry)
        torsion_moment = None
        angular_deflection = None
        if spring_type == "torsion":
            E = 200.0 * GPa_TO_N_MM2          # approximate E for steel, N/mm²
            torsion_moment = load_force_n * (D / 2.0)
            angular_deflection = (
                10.8 * torsion_moment * D * n_a / (E * d**4)
            ) * (180 / math.pi)

        # Free-length constraint check
        if max_free_length_mm is not None and L0 > max_free_length_mm:
            logger.warning(
                "Computed free length %.2f mm exceeds constraint %.2f mm.",
                L0,
                max_free_length_mm,
            )

        geometry = {
            "wire_diameter_mm": round(d, 3),
            "mean_coil_diameter_mm": round(D, 3),
            "outer_diameter_mm": round(D + d, 3),
            "inner_diameter_mm": round(D - d, 3),
            "active_coils": round(n_a, 2),
            "total_coils": round(n_t, 2),
            "free_length_mm": round(L0, 3),
            "pitch_mm": round(pitch, 3),
            "spring_index": round(C, 3),
            "spring_rate_n_mm": round(k_actual, 4),
            "wahl_factor": round(Ks, 4),
            "corrected_shear_stress_mpa": round(
                Ks * _shear_stress(load_force_n, d, D), 3
            ),
            "slenderness_ratio": round(_slenderness_ratio(L0, D), 3),
            "torsion_moment_n_mm": (
                round(torsion_moment, 3) if torsion_moment else None
            ),
            "angular_deflection_deg": (
                round(angular_deflection, 3) if angular_deflection else None
            ),
            "optimizer_converged": bool(result.success),
            "optimizer_message": result.message,
        }
        return json.dumps({"status": "ok", "geometry": geometry})

    except Exception as exc:
        logger.exception("calculate_spring_geometry_tool failed")
        return json.dumps({"status": "error", "message": str(exc)})


# ─────────────────────────────────────────────────────────────────────────────
# Tool 2 — Material Properties Query
# ─────────────────────────────────────────────────────────────────────────────


def _normalise_material_name(name: str) -> str:
    """Lower-case, strip punctuation for fuzzy matching."""
    import re
    return re.sub(r"[^a-z0-9\s]", "", name.lower()).strip()


def _score_candidates(
    df: pd.DataFrame,
    *,
    operating_temperature_c: float,
    corrosion_resistant: bool,
    cyclic_load: bool,
    preferred_material_name: str | None,
) -> pd.DataFrame:
    """
    Multi-factor scoring:
      base_score = yield_strength_mpa / cost_usd_per_kg

      temp_bonus   = min(max_temp_c / max(operating_temperature_c, 25), 2.0)
      fatigue_bonus = 1.15 if cyclic_load and yield_strength_mpa >= 1500 else 1.0
      corrosion_bonus = 1.0 (already filtered — no extra weight)
      preference_bonus = 1.5 if the material name matches preferred_material_name

      composite = base_score × temp_bonus × fatigue_bonus × preference_bonus
    """
    df = df.copy()

    temp_margin = df["max_temp_c"] / max(operating_temperature_c, 25.0)
    df["temp_bonus"] = temp_margin.clip(lower=1.0, upper=2.0).round(3)

    if cyclic_load:
        df["fatigue_bonus"] = df["yield_strength_mpa"].apply(
            lambda sy: 1.15 if sy >= 1500 else 1.0
        )
    else:
        df["fatigue_bonus"] = 1.0

    if preferred_material_name:
        preferred_normalized = _normalise_material_name(preferred_material_name)
        df["preference_bonus"] = df["name"].apply(
            lambda n: 1.5 if preferred_normalized in _normalise_material_name(n) else 1.0
        )
    else:
        df["preference_bonus"] = 1.0

    base = df["yield_strength_mpa"] / df["cost_usd_per_kg"]
    df["score"] = (base * df["temp_bonus"] * df["fatigue_bonus"] * df["preference_bonus"]).round(3)

    # Keep individual bonus columns for transparency
    df = df.sort_values("score", ascending=False).reset_index(drop=True)
    return df


@tool
def query_material_properties_tool(
    operating_temperature_c: float = 25.0,
    corrosion_resistant: bool = False,
    cyclic_load: bool = False,
    max_cost_usd_per_kg: float | None = None,
    spring_type: str = "compression",
    preferred_material_name: str | None = None,
) -> str:
    """
    Query the PostgreSQL materials catalogue and return the best-matching
    materials ranked by suitability.

    In production this executes a parameterised SQL query against the
    ``spring_materials`` table.  The stub implementation returns a representative
    dataset so the graph can be executed without a live database.

    Args:
        operating_temperature_c: Maximum operating temperature (°C).
        corrosion_resistant:     True if corrosion resistance is mandatory.
        cyclic_load:             True if fatigue life is critical.
        max_cost_usd_per_kg:     Optional cost ceiling (USD/kg).
        spring_type:             Spring type – influences alloy selection.
        preferred_material_name: User's expressed material preference (if any).
                                 Matching materials get a 1.5× scoring boost.

    Returns:
        JSON string with candidate list AND a rationale summary.
    """
    try:
        # ── Stub dataset (replace with asyncpg/SQLAlchemy query in production) ─
        ALL_MATERIALS: list[dict[str, Any]] = [
            {
                "material_id": 1,
                "name": "ASTM A228 Music Wire",
                "shear_modulus_gpa": 81.5,
                "elastic_modulus_gpa": 207.0,
                "density_kg_m3": 7850.0,
                "yield_strength_mpa": 1580.0,
                "ultimate_strength_mpa": 1900.0,
                "max_temp_c": 120.0,
                "corrosion_resistant": False,
                "cost_usd_per_kg": 3.80,
            },
            {
                "material_id": 2,
                "name": "ASTM A227 Hard-Drawn Wire",
                "shear_modulus_gpa": 79.3,
                "elastic_modulus_gpa": 200.0,
                "density_kg_m3": 7850.0,
                "yield_strength_mpa": 1100.0,
                "ultimate_strength_mpa": 1380.0,
                "max_temp_c": 120.0,
                "corrosion_resistant": False,
                "cost_usd_per_kg": 2.10,
            },
            {
                "material_id": 3,
                "name": "ASTM A313 Type 302 Stainless Steel",
                "shear_modulus_gpa": 69.0,
                "elastic_modulus_gpa": 193.0,
                "density_kg_m3": 7920.0,
                "yield_strength_mpa": 1100.0,
                "ultimate_strength_mpa": 1380.0,
                "max_temp_c": 260.0,
                "corrosion_resistant": True,
                "cost_usd_per_kg": 9.50,
            },
            {
                "material_id": 4,
                "name": "ASTM B197 Phosphor Bronze",
                "shear_modulus_gpa": 41.4,
                "elastic_modulus_gpa": 103.0,
                "density_kg_m3": 8860.0,
                "yield_strength_mpa": 510.0,
                "ultimate_strength_mpa": 640.0,
                "max_temp_c": 95.0,
                "corrosion_resistant": True,
                "cost_usd_per_kg": 14.20,
            },
            {
                "material_id": 5,
                "name": "ASTM A401 Chrome-Silicon (SAE 9254)",
                "shear_modulus_gpa": 77.2,
                "elastic_modulus_gpa": 200.0,
                "density_kg_m3": 7850.0,
                "yield_strength_mpa": 1720.0,
                "ultimate_strength_mpa": 2000.0,
                "max_temp_c": 245.0,
                "corrosion_resistant": False,
                "cost_usd_per_kg": 5.60,
            },
            {
                "material_id": 6,
                "name": "DIN 17223-C Chrome-Vanadium (VD-SiCr)",
                "shear_modulus_gpa": 78.5,
                "elastic_modulus_gpa": 206.0,
                "density_kg_m3": 7850.0,
                "yield_strength_mpa": 1650.0,
                "ultimate_strength_mpa": 1950.0,
                "max_temp_c": 220.0,
                "corrosion_resistant": False,
                "cost_usd_per_kg": 6.90,
            },
            {
                "material_id": 7,
                "name": "Inconel 718 (High-Temp)",
                "shear_modulus_gpa": 77.0,
                "elastic_modulus_gpa": 200.0,
                "density_kg_m3": 8190.0,
                "yield_strength_mpa": 1100.0,
                "ultimate_strength_mpa": 1380.0,
                "max_temp_c": 590.0,
                "corrosion_resistant": True,
                "cost_usd_per_kg": 95.00,
            },
        ]

        df = pd.DataFrame(ALL_MATERIALS)

        # ── SQL-equivalent filters ─────────────────────────────────────────
        df = df[df["max_temp_c"] >= operating_temperature_c]

        if corrosion_resistant:
            df = df[df["corrosion_resistant"] == True]  # noqa: E712

        if max_cost_usd_per_kg is not None:
            df = df[df["cost_usd_per_kg"] <= max_cost_usd_per_kg]

        if df.empty:
            return json.dumps({
                "status": "no_match",
                "message": (
                    "No material in the catalogue satisfies all constraints. "
                    "Consider relaxing temperature, corrosion, or cost requirements."
                ),
            })

        # ── Multi-factor scoring ───────────────────────────────────────────
        df = _score_candidates(
            df,
            operating_temperature_c=operating_temperature_c,
            corrosion_resistant=corrosion_resistant,
            cyclic_load=cyclic_load,
            preferred_material_name=preferred_material_name,
        )

        candidates = df.to_dict(orient="records")
        return json.dumps({"status": "ok", "candidates": candidates})

    except Exception as exc:
        logger.exception("query_material_properties_tool failed")
        return json.dumps({"status": "error", "message": str(exc)})


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
        safety_shear = (0.45 * Sy) / tau if tau > 0 else float("inf")

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
        if safety_shear + 1e-4 < 1.3:
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
            Sut_approx = 1.25 * Sy
            Ses = 0.324 * Sut_approx  # endurance limit in shear
            Ssy = 0.45 * Sy           # torsional yield limit
            # Goodman: (tau_alt/Ses) + (tau_mean/Ssy) = 1 at failure
            goodman_lhs = (tau_alt / Ses) + (tau_mean / Ssy)
            safety_fatigue = 1.0 / goodman_lhs if goodman_lhs > 0 else float("inf")
            if safety_fatigue < 1.3:
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


# ─────────────────────────────────────────────────────────────────────────────
# Tool 4 — Commercial Scoring
# ─────────────────────────────────────────────────────────────────────────────


@tool
def commercial_scoring_tool(proposals: str) -> str:
    """
    Rank a list of spring design proposals by a weighted commercial index.

    The composite score balances material cost and estimated durability.
    Output is structured as a JSON array ready for React/Recharts rendering
    and includes a ``three_js_params`` sub-object for 3D model generation.

    Args:
        proposals: JSON string representing a list of proposal dicts.
                   Each dict must contain at minimum:
                   - ``proposal_id``         (str)
                   - ``wire_diameter_mm``    (float)
                   - ``mean_coil_diameter_mm`` (float)
                   - ``active_coils``        (float)
                   - ``total_coils``         (float)
                   - ``free_length_mm``      (float)
                   - ``outer_diameter_mm``   (float)
                   - ``density_kg_m3``       (float)
                   - ``cost_usd_per_kg``     (float)
                   - ``safety_factor_shear`` (float)
                   - ``safety_factor_buckling`` (float)
                   - ``cycles_expected``     (int, optional)

    Returns:
        JSON string with ranked proposals list and chart-ready data.
    """
    try:
        raw: list[dict[str, Any]] = json.loads(proposals)
        df = pd.DataFrame(raw)

        # ── Wire mass = (π/4)*d²*(π*D*n_t)  [mm³ → kg] ────────────────────
        df["wire_volume_mm3"] = (
            (math.pi / 4.0) * df["wire_diameter_mm"] ** 2
            * math.pi
            * df["mean_coil_diameter_mm"]
            * df["total_coils"]
        )
        df["wire_mass_kg"] = df["wire_volume_mm3"] * df["density_kg_m3"] / 1e9

        # ── Material cost per spring [USD] ─────────────────────────────────
        df["material_cost_usd"] = df["wire_mass_kg"] * df["cost_usd_per_kg"]

        # ── Estimated life cycles (stub: Sf × baseline) ────────────────────
        baseline_cycles = 500_000
        df["estimated_life_cycles"] = (
            df["safety_factor_shear"] * baseline_cycles
        ).astype(int)

        # ── Composite score  (higher = better) ────────────────────────────
        # Weights: cost efficiency 40%, shear safety 30%, buckling safety 20%,
        #          free-length compactness 10%
        max_cost = df["material_cost_usd"].max()
        max_sf = df["safety_factor_shear"].max()
        max_sfb = df["safety_factor_buckling"].max()
        max_L0 = df["free_length_mm"].max()

        df["score_cost"] = 1.0 - (df["material_cost_usd"] / max_cost)
        df["score_shear"] = df["safety_factor_shear"] / max_sf
        df["score_buckling"] = df["safety_factor_buckling"] / max_sfb
        df["score_compactness"] = 1.0 - (df["free_length_mm"] / max_L0)

        df["composite_score"] = (
            0.40 * df["score_cost"]
            + 0.30 * df["score_shear"]
            + 0.20 * df["score_buckling"]
            + 0.10 * df["score_compactness"]
        )

        df = df.sort_values("composite_score", ascending=False).reset_index(drop=True)
        df["rank"] = df.index + 1

        # ── Chart-ready payload (Recharts / Victory format) ────────────────
        chart_data = df[[
            "proposal_id", "rank", "composite_score",
            "material_cost_usd", "estimated_life_cycles",
            "safety_factor_shear", "safety_factor_buckling",
            "wire_mass_kg",
        ]].round(4).to_dict(orient="records")

        # ── Three.js geometry parameters ───────────────────────────────────
        def three_js_params(row: pd.Series) -> dict[str, Any]:
            return {
                "wireRadius": round(row["wire_diameter_mm"] / 2.0, 3),
                "coilRadius": round(row["mean_coil_diameter_mm"] / 2.0, 3),
                "totalCoils": round(row["total_coils"], 2),
                "height": round(row["free_length_mm"], 3),
                "tubeSegments": 64,
                "radialSegments": 16,
            }

        ranked_proposals = []
        for _, row in df.iterrows():
            ranked_proposals.append({
                "proposal_id": row["proposal_id"],
                "rank": int(row["rank"]),
                "composite_score": round(row["composite_score"], 4),
                "wire_mass_kg": round(row["wire_mass_kg"], 6),
                "material_cost_usd": round(row["material_cost_usd"], 4),
                "estimated_life_cycles": int(row["estimated_life_cycles"]),
                "three_js_params": three_js_params(row),
            })

        return json.dumps({
            "status": "ok",
            "ranked_proposals": ranked_proposals,
            "chart_data": chart_data,
        })

    except Exception as exc:
        logger.exception("commercial_scoring_tool failed")
        return json.dumps({"status": "error", "message": str(exc)})


# ─────────────────────────────────────────────────────────────────────────────
# Tool registry (makes discovery easy for ToolNode)
# ─────────────────────────────────────────────────────────────────────────────

ALL_TOOLS = [
    calculate_spring_geometry_tool,
    query_material_properties_tool,
    compliance_verification_tool,
    commercial_scoring_tool,
]
