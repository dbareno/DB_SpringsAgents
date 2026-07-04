"""
app/tools/geometry.py
─────────────────────────────────────────────────────────────────────────────
LangChain @tool definitions for helical spring geometry calculation and
redesign advice.

Each tool is a self-contained callable decorated with @tool so LangGraph's
ToolNode can invoke it automatically when an agent emits a ToolCall.
"""

from __future__ import annotations

import json
import logging
import math

import numpy as np
from scipy.optimize import OptimizeResult  # type: ignore[import]
from scipy.optimize import differential_evolution  # type: ignore[import]

from app.tools.physics import (
    FATIGUE_MIN_LOAD_RATIO,
    GOODMAN_TARGET_SF,
    GPa_TO_N_MM2,
    SHEAR_ENDURANCE_FACTOR,
    TORSIONAL_YIELD_FACTOR,
    ULTIMATE_FROM_YIELD_FACTOR,
    _shear_stress,
    _slenderness_ratio,
    _spring_rate,
    _wahl_correction,
)

from langchain_core.tools import tool

logger = logging.getLogger(__name__)


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
    cyclic_load: bool = False,
) -> str:
    """
    Compute optimal helical spring geometry using scipy.optimize.minimize.

    This tool solves for the best combination of wire diameter (d), mean coil
    diameter (D), and active coil count (n_a) that satisfies the required
    spring rate while minimising total wire volume (∝ material cost).

    When ``cyclic_load=True``, the optimizer also rejects geometries that
    fail the Goodman fatigue criterion (Sf_fatigue ≥ 1.3), preventing the
    loop of "stronger material → thinner wire → worse fatigue."

    Args:
        spring_type:          One of 'compression', 'extension', 'torsion'.
        load_force_n:         Required operating load in Newtons.
        deflection_mm:        Required deflection at that load in mm.
        max_outer_diameter_mm: Hard OD constraint (mm). None = unconstrained.
        max_free_length_mm:   Hard free-length constraint (mm). None = unconstrained.
        shear_modulus_gpa:    G of the chosen material in GPa (default: 79.3 = steel).
        yield_strength_mpa:   Sy of the material in MPa (default: 1500).
        dead_coils:           Non-active (dead) coils at each end (default: 2.0 total).
        cyclic_load:          If True, rejects designs failing Goodman fatigue Sf ≥ 1.3.

    Returns:
        JSON string with the computed geometry dict or an error message.
    """
    try:
        G = shear_modulus_gpa * GPa_TO_N_MM2          # → N/mm²
        k_target = load_force_n / deflection_mm        # required spring rate N/mm

        # ── Estrategia de optimización ──────────────────────────────────────
        # El problema 3D (d, D, n_a) con un equality constraint (spring rate)
        # es mal condicionado porque d y D tienen escalas muy distintas y la
        # spring rate depende de d⁴ / D³, creando una superficie casi singular.
        #
        # Solución: eliminar n_a como variable independiente. La spring rate
        # determina n_a de forma única:
        #     k = G·d⁴ / (8·D³·n_a)  →  n_a = G·d⁴ / (8·D³·k)
        #
        # Esto reduce el problema a 2 variables (d, D) sin equality constraints
        # y permite usar differential_evolution (global, sin gradientes).

        # Standard requires Sf ≥ 1.3 for shear (DIN 2095 / EN 13906-1)
        ALLOWABLE_SHEAR_MPA = (
            TORSIONAL_YIELD_FACTOR * yield_strength_mpa / GOODMAN_TARGET_SF
        )

        # ── Derived n_a ─────────────────────────────────────────────────────
        def _active_coils(d: float, D: float) -> float:
            """Derive active coils from spring-rate target (exact)."""
            return G * d**4 / (8.0 * D**3 * k_target)

        # ── Objective with penalty (differential_evolution no acepta
        #     constraints como callable en scipy < 1.?.?). Penalizamos
        #     puntos inviables con volumen enorme para que DE los descarte. ─
        PENALTY = 1e12

        # ── Pre-compute Goodman fatigue constants (if cyclic) ───────────────
        if cyclic_load and load_force_n > 0:
            _F_min = FATIGUE_MIN_LOAD_RATIO * load_force_n
            _F_max = load_force_n
            _F_mean = (_F_max + _F_min) / 2.0
            _F_alt = (_F_max - _F_min) / 2.0
            _Sut_approx = ULTIMATE_FROM_YIELD_FACTOR * yield_strength_mpa
            _Ses = SHEAR_ENDURANCE_FACTOR * _Sut_approx  # endurance limit in shear
            _Ssy = TORSIONAL_YIELD_FACTOR * yield_strength_mpa

            def _goodman_ok(d: float, D_: float, Ks: float) -> bool:
                """Goodman fatigue Sf >= 1.3 (same assumption as Agent 4)."""
                tau_mean = Ks * _shear_stress(_F_mean, d, D_)
                tau_alt = Ks * _shear_stress(_F_alt, d, D_)
                goodman_lhs = (tau_alt / _Ses) + (tau_mean / _Ssy)
                sf_fatigue = 1.0 / goodman_lhs if goodman_lhs > 0 else float("inf")
                return sf_fatigue >= GOODMAN_TARGET_SF
        else:
            def _goodman_ok(d: float, D_: float, Ks: float) -> bool:
                return True

        def _volume(x: np.ndarray) -> float:
            d, D_ = float(x[0]), float(x[1])
            if d <= 0 or D_ <= 0:
                return PENALTY
            n_a = _active_coils(d, D_)
            if n_a < 1.0 or n_a > 60.0:
                return PENALTY
            C = D_ / d
            if C < 4.0 or C > 12.0:
                return PENALTY
            # Shear stress safety: Ks·τ ≤ allowable
            Ks = _wahl_correction(C)
            tau = Ks * _shear_stress(load_force_n, d, D_)
            if tau > ALLOWABLE_SHEAR_MPA:
                return PENALTY
            if not _goodman_ok(d, D_, Ks):
                return PENALTY
            # OD constraint
            if max_outer_diameter_mm is not None and (D_ + d) > max_outer_diameter_mm:
                return PENALTY
            # Free-length constraint
            if max_free_length_mm is not None:
                n_t = n_a + dead_coils
                L0_est = n_t * d + deflection_mm + 2.0 * d
                if L0_est > max_free_length_mm:
                    return PENALTY
            # Pasa todas las constraints → volumen real
            n_t = n_a + dead_coils
            return (math.pi**2 / 4.0) * d**2 * D_ * n_t

        # ── Bounds ──────────────────────────────────────────────────────────
        d_bounds = (0.5, 20.0)
        D_max = 200.0
        if max_outer_diameter_mm is not None:
            D_max = max(5.0, max_outer_diameter_mm - 0.5)
        D_bounds = (5.0, D_max)

        # ── Run differential_evolution ──────────────────────────────────────
        result: OptimizeResult = differential_evolution(
            _volume,
            bounds=[d_bounds, D_bounds],
            seed=42,
            maxiter=1_000,
            tol=1e-10,
            popsize=30,
            mutation=(0.5, 1.5),
            recombination=0.9,
            polish=False,
        )

        d, D = result.x
        n_a = _active_coils(d, D)

        # ── Verificación + fallback determinístico ──────────────────────────
        # Si DE no encontró punto factible, hacemos grid search sobre C y d.
        def _feasible(d: float, D: float) -> bool:
            if d <= 0 or D <= 0:
                return False
            n = _active_coils(d, D)
            if n < 2.0 or n > 60.0:
                return False
            C = D / d
            if C < 4.0 or C > 12.0:
                return False
            Ks = _wahl_correction(C)
            tau = Ks * _shear_stress(load_force_n, d, D)
            if tau > ALLOWABLE_SHEAR_MPA:
                return False
            if not _goodman_ok(d, D, Ks):
                return False
            if max_outer_diameter_mm is not None and (D + d) > max_outer_diameter_mm + 0.01:
                return False
            if max_free_length_mm is not None:
                n_t = n + dead_coils
                L0_est = n_t * d + deflection_mm + 2.0 * d
                if L0_est > max_free_length_mm + 0.01:
                    return False
            return True

        if not _feasible(d, D):
            logger.warning(
                "DE opt result infeasible (d=%.3f, D=%.3f, n_a=%.1f) "
                "— running grid fallback.",
                d, D, n_a,
            )
            # Grid search sobre C (4.0–12.0) y d (0.5–max_OD/2)
            best = None
            best_vol = float("inf")
            d_max_grid = 20.0
            if max_outer_diameter_mm is not None:
                d_max_grid = max_outer_diameter_mm / 2.0
            for C_ in [x * 0.5 for x in range(8, 25)]:  # C: 4.0–12.0 step 0.5
                if max_outer_diameter_mm is not None:
                    # Para este C, el diámetro máximo está limitado por OD
                    d_ub = min(d_max_grid, max_outer_diameter_mm / (1.0 + C_))
                else:
                    d_ub = d_max_grid
                for d_ in [x * 0.05 for x in range(10, int(d_ub / 0.05) + 1)]:
                    D_ = C_ * d_
                    if not _feasible(d_, D_):
                        continue
                    n = _active_coils(d_, D_)
                    n_t = n + dead_coils
                    vol = (math.pi**2 / 4.0) * d_**2 * D_ * n_t
                    if vol < best_vol:
                        best_vol = vol
                        best = (d_, D_, n)
            if best is not None:
                d, D, n_a = best
                result.success = True
                result.message = "Grid search fallback"
                logger.warning("Grid fallback found feasible geometry.")
            else:
                # No hay solución factible: reportar error
                logger.error("No feasible geometry exists for these constraints.")
                msg_parts = []
                if max_outer_diameter_mm is not None:
                    msg_parts.append(f"OD ≤ {max_outer_diameter_mm}mm")
                if max_free_length_mm is not None:
                    msg_parts.append(f"FL ≤ {max_free_length_mm}mm")
                msg_parts.append(f"F={load_force_n}N, δ={deflection_mm}mm")
                return json.dumps({
                    "status": "error",
                    "message": (
                        f"Cannot design a spring satisfying all constraints "
                        f"({' + '.join(msg_parts)}). Try relaxing OD, FL, "
                        f"or increasing allowable stress (stronger material)."
                    ),
                })

        # ── Build output ────────────────────────────────────────────────────
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
            "safety_factor_shear": round(
                TORSIONAL_YIELD_FACTOR * yield_strength_mpa
                / max(Ks * _shear_stress(load_force_n, d, D), 1e-9), 3
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
# Tool 4 — Redesign Advisor (feedback numérico exacto)
# ─────────────────────────────────────────────────────────────────────────────


@tool
def redesign_advisor_tool(
    wire_diameter_mm: float,
    mean_coil_diameter_mm: float,
    active_coils: float,
    free_length_mm: float,
    load_force_n: float,
    deflection_mm: float,
    yield_strength_mpa: float,
    max_outer_diameter_mm: float | None = None,
    max_free_length_mm: float | None = None,
    safety_factor_shear: float | None = None,
    safety_factor_buckling: float | None = None,
    safety_factor_fatigue: float | None = None,
    slenderness_ratio: float | None = None,
    failure_modes: str = "[]",
) -> str:
    """
    Redesign advisor: traduce fallos de compliance en ajustes NUMÉRICOS
    exactos para el siguiente ciclo de rediseño.

    A diferencia del enfoque anterior (LLM adivinando ajustes), este tool
    computa DERIVADAS ANALÍTICAS para calcular cuánto cambiar cada parámetro:

      • Sf_shear = (0.45·Sy) / (Ks·8·F·D/(π·d³))  ∝  Sy·d³ / D
        → Para subir Sf de X a Y: si ajustamos d → Δd = d·((X/Y)^(1/3) - 1)
                                 si ajustamos Sy → Sy_requerido = Sy·(Y/X)

      • Buckling:  λ = L0/D.  Para λ ≤ 5.26:
        → ΔD = D·(λ/5.26 - 1)  o  ΔL0 = min(L0·(1 - 5.26/λ), L0*0.3)

    Returns:
        JSON con adjustments, material_constraints, y una sugerencia
        de acción (re-run Agent 3, relajar constraint, etc.)
    """
    try:
        d = wire_diameter_mm
        D = mean_coil_diameter_mm
        n_a = active_coils
        L0 = free_length_mm
        F = load_force_n
        delta = deflection_mm
        Sy = yield_strength_mpa
        Sf_target = GOODMAN_TARGET_SF

        import json as _json

        failure_list: list[str] = _json.loads(failure_modes) if isinstance(failure_modes, str) else []

        adjustments: dict[str, float] = {}   # campo → delta_%
        material_constraints: dict[str, object] = {}
        suggestions: list[str] = []

        # ── 1. Shear safety factor ────────────────────────────────────────
        sf_shear = safety_factor_shear if safety_factor_shear else 0.0
        if sf_shear > 0 and sf_shear < Sf_target - 1e-4:
            ratio = Sf_target / sf_shear  # ej: 1.30/1.05 = 1.238

            # Opción A: aumentar wire diameter → Sf ∝ d³ → Δd = ratio^(1/3) - 1
            d_factor = ratio ** (1.0 / 3.0)  # ~1.074 para ratio=1.238
            d_delta_pct = round((d_factor - 1.0) * 100.0, 1)

            # Opción B: aumentar yield strength → Sf ∝ Sy → Sy_needed = Sy * ratio
            sy_needed = round(Sy * ratio, 0)

            # Opción C: reducir D → Sf ∝ 1/D → D_delta = 1 - 1/ratio
            D_delta_pct = round((1.0 - 1.0 / ratio) * 100.0, 1)

            adjustments["wire_diameter_mm"] = d_delta_pct
            adjustments["mean_coil_diameter_mm"] = -D_delta_pct  # reducir
            material_constraints["min_yield_strength_mpa"] = sy_needed
            suggestions.append(
                f"Shear Sf={sf_shear:.3f} < {Sf_target}: aumentar d en un ~{d_delta_pct}%, "
                f"o reducir D en un ~{D_delta_pct}%, o usar material con Sy≥{sy_needed:.0f}MPa "
                f"(actual Sy={Sy:.0f}MPa)."
            )

        # ── 1b. Fatigue (Goodman) ─────────────────────────────────────────
        sf_fatigue = safety_factor_fatigue if safety_factor_fatigue else None
        if sf_fatigue is not None and sf_fatigue < Sf_target - 1e-4:
            ratio = Sf_target / sf_fatigue
            # Fatigue Sf ∝ d³/D (same as shear). But can also improve by
            # reducing the load range (less practical at this level).
            # Recommend the same wire/strength adjustments as shear.
            d_factor = ratio ** (1.0 / 3.0)
            d_delta_pct = round((d_factor - 1.0) * 100.0, 1)
            sy_needed = round(Sy * ratio, 0)

            # Take the more aggressive of shear vs fatigue adjustments
            existing_d = adjustments.get("wire_diameter_mm", 0.0)
            if abs(d_delta_pct) > abs(existing_d):
                adjustments["wire_diameter_mm"] = d_delta_pct
            existing_sy = material_constraints.get("min_yield_strength_mpa", 0)
            if sy_needed > existing_sy:
                material_constraints["min_yield_strength_mpa"] = sy_needed

            suggestions.append(
                f"Fatigue Goodman Sf={sf_fatigue:.3f} < {Sf_target}: aumentar d en ~{d_delta_pct}%, "
                f"o usar material con Sy≥{sy_needed:.0f}MPa. "
                f"Shot peening también mejora vida en fatiga 2.5×."
            )

        # ── 2. Buckling ───────────────────────────────────────────────────
        lam = slenderness_ratio if slenderness_ratio else L0 / max(D, 0.001)
        CRITICAL_LAMBDA = 5.26
        if lam > CRITICAL_LAMBDA:
            # λ = L0/D. Para cumplir λ ≤ 5.26:
            #   Opción A: reducir L0 en ΔL0 = L0·(1 - 5.26/λ)
            l0_factor = CRITICAL_LAMBDA / lam
            l0_delta_pct = round((1.0 - l0_factor) * 100.0, 1)
            #   Opción B: aumentar D en ΔD = D·(λ/5.26 - 1)
            d_delta_buckling = round((lam / CRITICAL_LAMBDA - 1.0) * 100.0, 1)

            # Elegir la más práctica: reducir L0 si es posible
            if "free_length_mm" not in adjustments or abs(adjustments.get("free_length_mm", 0)) < l0_delta_pct:
                adjustments["free_length_mm"] = -l0_delta_pct
            # También sugerir aumentar D como alternativa
            if d_delta_buckling < abs(adjustments.get("mean_coil_diameter_mm", 0)):
                adjustments["mean_coil_diameter_mm"] = d_delta_buckling

            suggestions.append(
                f"Buckling λ={lam:.2f} > {CRITICAL_LAMBDA}: reducir L0 en ~{l0_delta_pct}% "
                f"o aumentar D en ~{d_delta_buckling}%."
            )

        # ── 3. Free-length constraint ─────────────────────────────────────
        if max_free_length_mm is not None and L0 > max_free_length_mm:
            excess_pct = round((L0 / max_free_length_mm - 1.0) * 100.0, 1)
            # Reducir L0: menos coils activos o menor wire diameter
            adjustments["free_length_mm"] = -excess_pct
            # También reducir n_a indirectamente vía wire_diameter
            # (menor d → menor solid length)
            suggestions.append(
                f"Free length {L0:.1f}mm > {max_free_length_mm:.1f}mm "
                f"(exceso ~{excess_pct}%): reducir n_a o d."
            )

        # ── 4. Spring index ───────────────────────────────────────────────
        has_index_failure = any("spring index" in fm.lower() for fm in failure_list)
        if has_index_failure:
            suggestions.append(
                "Spring index C fuera de rango [4, 12]: ajustar relación D/d. "
                "Generalmente aumentar d (bajar C) o reducir D (bajar C)."
            )

        # ── Build response ────────────────────────────────────────────────
        result: dict[str, object] = {
            "adjustments": adjustments,
            "material_constraints": material_constraints,
            "suggestions": suggestions,
        }
        if material_constraints:
            result["action"] = "re-run-agent-3"
        elif adjustments:
            result["action"] = "adjust-tool-input"
        else:
            result["action"] = "relax-constraints-or-stop"

        return json.dumps({"status": "ok", "advisor": result})

    except Exception as exc:
        logger.exception("redesign_advisor_tool failed")
        return json.dumps({"status": "error", "message": str(exc)})
