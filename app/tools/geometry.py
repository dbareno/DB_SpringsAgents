"""
app/tools/geometry.py
─────────────────────────────────────────────────────────────────────────────
LangChain @tool definitions for helical spring geometry calculation and
redesign advice.

Each tool is a self-contained callable decorated with @tool so LangGraph's
ToolNode can invoke it automatically when an agent emits a ToolCall.

Strategy pattern (Phase 4 — Engineering Depth)
───────────────────────────────────────────────
``calculate_spring_geometry_tool`` is a thin dispatcher: it selects a
``SpringDesignEngine`` implementation based on ``spring_type`` and delegates
all geometry/stress computation to it. This keeps the compression path
byte-for-byte identical to the pre-Phase-4 behavior (implemented in
``CompressionEngine``) while ``ExtensionEngine`` and ``TorsionEngine`` add
type-specific stress models without touching the tool's signature or the
``status``/``geometry`` JSON envelope.
"""

from __future__ import annotations

import json
import logging
import math
from abc import ABC, abstractmethod
from typing import Any

import numpy as np
from langchain_core.tools import tool
from scipy.optimize import (
    OptimizeResult,  # type: ignore[import]
    differential_evolution,  # type: ignore[import]
)

from app.tools.physics import (
    FATIGUE_MIN_LOAD_RATIO,
    GOODMAN_TARGET_SF,
    SHEAR_ENDURANCE_FACTOR,
    TORSIONAL_YIELD_FACTOR,
    ULTIMATE_FROM_YIELD_FACTOR,
    GPa_TO_N_MM2,
    _arm_bending_stress,
    _bending_correction,
    _coil_torsion_stress,
    _hook_bending_stress,
    _hook_torsion_stress,
    _shear_stress,
    _slenderness_ratio,
    _spring_rate,
    _torsion_correction,
    _wahl_correction,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Strategy interface — SpringDesignEngine
# ─────────────────────────────────────────────────────────────────────────────


class SpringDesignEngine(ABC):
    """
    Strategy interface for per-spring-type geometry design.

    Each concrete engine owns its own design variables, optimizer bounds,
    and stress model, but all engines return a geometry dict compatible
    with the shared ``calculate_spring_geometry_tool`` JSON envelope (same
    keys as the compression baseline, plus type-specific extra keys that
    default to ``None`` for other types).
    """

    #: Human-readable engine name, used for logging only.
    name: str = "base"

    @abstractmethod
    def compute_geometry(
        self,
        *,
        load_force_n: float,
        deflection_mm: float,
        shear_modulus_gpa: float,
        yield_strength_mpa: float,
        max_outer_diameter_mm: float | None = None,
        max_free_length_mm: float | None = None,
        dead_coils: float = 2.0,
        cyclic_load: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Solve for (d, D, n_a) and return the full geometry dict."""
        raise NotImplementedError

    @abstractmethod
    def compute_stress(self, d: float, D: float, n_a: float, **kwargs: Any) -> dict[str, Any]:
        """Return the type-specific stress dict for a given geometry."""
        raise NotImplementedError

    @abstractmethod
    def validate_constraints(self, d: float, D: float, n_a: float, **kwargs: Any) -> bool:
        """Return True if (d, D, n_a) is feasible under this engine's rules."""
        raise NotImplementedError


# ─────────────────────────────────────────────────────────────────────────────
# CompressionEngine — reference implementation (unchanged behavior)
# ─────────────────────────────────────────────────────────────────────────────


class CompressionEngine(SpringDesignEngine):
    """
    Compression spring engine — the original (pre-Phase-4) optimizer logic,
    moved here verbatim as the reference implementation. Design variables:
    wire diameter (d), mean coil diameter (D); active coils (n_a) is derived
    exactly from the target spring rate.

    Bounds: wire 0.5–20 mm, spring index C = D/d in [4, 12], active coils in
    [1, 60] (feasibility) / [2, 60] (final check).
    """

    name = "compression"

    D_INDEX_MIN = 4.0
    D_INDEX_MAX = 12.0
    N_A_MIN = 1.0
    N_A_MAX = 60.0
    D_WIRE_BOUNDS = (0.5, 20.0)

    def compute_stress(self, d: float, D: float, n_a: float, **kwargs: Any) -> dict[str, Any]:
        F = kwargs["load_force_n"]
        C = D / d
        Ks = _wahl_correction(C)
        tau = Ks * _shear_stress(F, d, D)
        return {"wahl_factor": Ks, "corrected_shear_stress_mpa": tau}

    def validate_constraints(self, d: float, D: float, n_a: float, **kwargs: Any) -> bool:
        if d <= 0 or D <= 0:
            return False
        if n_a < self.N_A_MIN or n_a > self.N_A_MAX:
            return False
        C = D / d
        return self.D_INDEX_MIN <= C <= self.D_INDEX_MAX

    def compute_geometry(
        self,
        *,
        load_force_n: float,
        deflection_mm: float,
        shear_modulus_gpa: float,
        yield_strength_mpa: float,
        max_outer_diameter_mm: float | None = None,
        max_free_length_mm: float | None = None,
        dead_coils: float = 2.0,
        cyclic_load: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        G = shear_modulus_gpa * GPa_TO_N_MM2
        k_target = load_force_n / deflection_mm

        ALLOWABLE_SHEAR_MPA = (
            TORSIONAL_YIELD_FACTOR * yield_strength_mpa / GOODMAN_TARGET_SF
        )

        def _active_coils(d: float, D: float) -> float:
            """Derive active coils from spring-rate target (exact)."""
            return G * d**4 / (8.0 * D**3 * k_target)

        PENALTY = 1e12

        if cyclic_load and load_force_n > 0:
            _F_min = FATIGUE_MIN_LOAD_RATIO * load_force_n
            _F_max = load_force_n
            _F_mean = (_F_max + _F_min) / 2.0
            _F_alt = (_F_max - _F_min) / 2.0
            _Sut_approx = ULTIMATE_FROM_YIELD_FACTOR * yield_strength_mpa
            _Ses = SHEAR_ENDURANCE_FACTOR * _Sut_approx
            _Ssy = TORSIONAL_YIELD_FACTOR * yield_strength_mpa

            def _goodman_ok(d: float, D_: float, Ks: float) -> bool:
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
            if n_a < self.N_A_MIN or n_a > self.N_A_MAX:
                return PENALTY
            C = D_ / d
            if C < self.D_INDEX_MIN or C > self.D_INDEX_MAX:
                return PENALTY
            Ks = _wahl_correction(C)
            tau = Ks * _shear_stress(load_force_n, d, D_)
            if tau > ALLOWABLE_SHEAR_MPA:
                return PENALTY
            if not _goodman_ok(d, D_, Ks):
                return PENALTY
            if max_outer_diameter_mm is not None and (D_ + d) > max_outer_diameter_mm:
                return PENALTY
            if max_free_length_mm is not None:
                n_t = n_a + dead_coils
                L0_est = n_t * d + deflection_mm + 2.0 * d
                if L0_est > max_free_length_mm:
                    return PENALTY
            n_t = n_a + dead_coils
            return (math.pi**2 / 4.0) * d**2 * D_ * n_t

        d_bounds = self.D_WIRE_BOUNDS
        D_max = 200.0
        if max_outer_diameter_mm is not None:
            D_max = max(5.0, max_outer_diameter_mm - 0.5)
        D_bounds = (5.0, D_max)

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

        def _feasible(d: float, D: float) -> bool:
            if d <= 0 or D <= 0:
                return False
            n = _active_coils(d, D)
            if n < 2.0 or n > self.N_A_MAX:
                return False
            C = D / d
            if C < self.D_INDEX_MIN or C > self.D_INDEX_MAX:
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
            best = None
            best_vol = float("inf")
            d_max_grid = 20.0
            if max_outer_diameter_mm is not None:
                d_max_grid = max_outer_diameter_mm / 2.0
            for C_ in [x * 0.5 for x in range(8, 25)]:
                if max_outer_diameter_mm is not None:
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
                logger.error("No feasible geometry exists for these constraints.")
                msg_parts = []
                if max_outer_diameter_mm is not None:
                    msg_parts.append(f"OD ≤ {max_outer_diameter_mm}mm")
                if max_free_length_mm is not None:
                    msg_parts.append(f"FL ≤ {max_free_length_mm}mm")
                msg_parts.append(f"F={load_force_n}N, δ={deflection_mm}mm")
                raise ValueError(
                    f"Cannot design a spring satisfying all constraints "
                    f"({' + '.join(msg_parts)}). Try relaxing OD, FL, "
                    f"or increasing allowable stress (stronger material)."
                )

        n_t = n_a + dead_coils
        C = D / d
        Ks = _wahl_correction(C)
        k_actual = _spring_rate(d, D, n_a, G)
        L0_solid = n_t * d
        pitch = (deflection_mm / n_a) + d
        L0 = L0_solid + deflection_mm + 2 * d

        if max_free_length_mm is not None and L0 > max_free_length_mm:
            logger.warning(
                "Computed free length %.2f mm exceeds constraint %.2f mm.",
                L0,
                max_free_length_mm,
            )

        tau = Ks * _shear_stress(load_force_n, d, D)

        return {
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
            "corrected_shear_stress_mpa": round(tau, 3),
            "safety_factor_shear": round(
                TORSIONAL_YIELD_FACTOR * yield_strength_mpa / max(tau, 1e-9), 3
            ),
            "slenderness_ratio": round(_slenderness_ratio(L0, D), 3),
            "torsion_moment_n_mm": None,
            "angular_deflection_deg": None,
            "hook_bending_stress_mpa": None,
            "hook_torsion_stress_mpa": None,
            "initial_tension_n": None,
            "arm_bending_stress_mpa": None,
            "coil_torsion_stress_mpa": None,
            "optimizer_converged": bool(result.success),
            "optimizer_message": result.message,
        }


# ─────────────────────────────────────────────────────────────────────────────
# ExtensionEngine — hook stress + initial tension
# ─────────────────────────────────────────────────────────────────────────────


class ExtensionEngine(SpringDesignEngine):
    """
    Extension spring engine. Design variables: wire diameter (d), mean coil
    diameter (D); active coils derived from the *working* spring rate
    k = (F - F_initial) / deflection, since initial tension is the preload
    that must be overcome before the coils start to open [Shigley, Ch. 10].

    Primary failure mode: hook bending stress at point A (tensile + bending
    superposed on the curved hook). Secondary: coil body shear (still
    checked, same Wahl-corrected formula as compression) and hook torsional
    shear at point B.

    Bounds: wire 0.8–6 mm (finer tolerance for tensile), spring index
    C = D/d in [3, 10] (looser, for hook clearance), active coils >= 2.
    """

    name = "extension"

    D_INDEX_MIN = 3.0
    D_INDEX_MAX = 10.0
    N_A_MIN = 2.0
    N_A_MAX = 30.0
    D_WIRE_BOUNDS = (0.8, 6.0)

    def compute_stress(self, d: float, D: float, n_a: float, **kwargs: Any) -> dict[str, Any]:
        F = kwargs["load_force_n"]
        C = D / d
        # Hook bend radius ratios: approximate r1 ≈ D/2 (bend follows coil
        # mean radius) and r2 ≈ d (tight secondary bend at point B), which is
        # the standard textbook simplification for a full-loop hook.
        C1 = C
        C2 = max(2.0 * 1.0, 2.0)  # r2 ≈ d → C2 = 2*r2/d ≈ 2.0
        Kb = _bending_correction(C1)
        Kw = _torsion_correction(C2)
        sigma_hook = _hook_bending_stress(F, d, D, Kb)
        tau_hook = _hook_torsion_stress(F, d, D, Kw)
        Ks_body = _wahl_correction(C)
        tau_body = Ks_body * _shear_stress(F, d, D)
        return {
            "hook_bending_stress_mpa": sigma_hook,
            "hook_torsion_stress_mpa": tau_hook,
            "wahl_factor": Ks_body,
            "corrected_shear_stress_mpa": tau_body,
        }

    def validate_constraints(self, d: float, D: float, n_a: float, **kwargs: Any) -> bool:
        if d <= 0 or D <= 0:
            return False
        if n_a < self.N_A_MIN or n_a > self.N_A_MAX:
            return False
        C = D / d
        return self.D_INDEX_MIN <= C <= self.D_INDEX_MAX

    def compute_geometry(
        self,
        *,
        load_force_n: float,
        deflection_mm: float,
        shear_modulus_gpa: float,
        yield_strength_mpa: float,
        max_outer_diameter_mm: float | None = None,
        max_free_length_mm: float | None = None,
        dead_coils: float = 0.0,
        cyclic_load: bool = False,
        initial_tension_n: float = 0.0,
        **kwargs: Any,
    ) -> dict[str, Any]:
        G = shear_modulus_gpa * GPa_TO_N_MM2
        F_i = max(0.0, initial_tension_n)
        # Working spring rate uses the load ABOVE initial tension — the coils
        # don't start opening until F > F_i [Shigley eq. 10-31].
        F_working = max(load_force_n - F_i, 1e-6)
        k_target = F_working / deflection_mm

        ALLOWABLE_HOOK_MPA = yield_strength_mpa / GOODMAN_TARGET_SF
        ALLOWABLE_SHEAR_MPA = (
            TORSIONAL_YIELD_FACTOR * yield_strength_mpa / GOODMAN_TARGET_SF
        )
        PENALTY = 1e12

        def _active_coils(d: float, D: float) -> float:
            return G * d**4 / (8.0 * D**3 * k_target)

        def _stresses(d: float, D: float) -> tuple[float, float, float]:
            C = D / d
            Kb = _bending_correction(C)
            Kw = _torsion_correction(2.0)
            sigma_hook = _hook_bending_stress(load_force_n, d, D, Kb)
            Ks_body = _wahl_correction(C)
            tau_body = Ks_body * _shear_stress(load_force_n, d, D)
            return sigma_hook, tau_body, Kw

        def _volume(x: np.ndarray) -> float:
            d, D_ = float(x[0]), float(x[1])
            if d <= 0 or D_ <= 0:
                return PENALTY
            n_a = _active_coils(d, D_)
            if n_a < self.N_A_MIN or n_a > self.N_A_MAX:
                return PENALTY
            C = D_ / d
            if C < self.D_INDEX_MIN or C > self.D_INDEX_MAX:
                return PENALTY
            sigma_hook, tau_body, Kw = _stresses(d, D_)
            if sigma_hook > ALLOWABLE_HOOK_MPA:
                return PENALTY
            if tau_body > ALLOWABLE_SHEAR_MPA:
                return PENALTY
            tau_hook = Kw * 8.0 * load_force_n * D_ / (math.pi * d**3)
            if tau_hook > ALLOWABLE_SHEAR_MPA:
                return PENALTY
            if max_outer_diameter_mm is not None and (D_ + d) > max_outer_diameter_mm:
                return PENALTY
            if max_free_length_mm is not None:
                L0_est = n_a * d + deflection_mm + 4.0 * D_
                if L0_est > max_free_length_mm:
                    return PENALTY
            n_t = n_a
            return (math.pi**2 / 4.0) * d**2 * D_ * n_t

        d_bounds = self.D_WIRE_BOUNDS
        D_max = 100.0
        if max_outer_diameter_mm is not None:
            D_max = max(3.0, max_outer_diameter_mm - 0.5)
        D_bounds = (3.0, D_max)

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
        n_a = max(_active_coils(d, D), self.N_A_MIN)

        def _feasible(d: float, D: float) -> bool:
            if d <= 0 or D <= 0:
                return False
            n = _active_coils(d, D)
            if n < self.N_A_MIN or n > self.N_A_MAX:
                return False
            C = D / d
            if C < self.D_INDEX_MIN or C > self.D_INDEX_MAX:
                return False
            sigma_hook, tau_body, Kw = _stresses(d, D)
            if sigma_hook > ALLOWABLE_HOOK_MPA:
                return False
            if tau_body > ALLOWABLE_SHEAR_MPA:
                return False
            tau_hook = Kw * 8.0 * load_force_n * D / (math.pi * d**3)
            if tau_hook > ALLOWABLE_SHEAR_MPA:
                return False
            if max_outer_diameter_mm is not None and (D + d) > max_outer_diameter_mm + 0.01:
                return False
            return True

        if not _feasible(d, D):
            logger.warning(
                "Extension DE opt infeasible (d=%.3f, D=%.3f) — running grid fallback.",
                d, D,
            )
            best = None
            best_vol = float("inf")
            d_max_grid = 6.0
            if max_outer_diameter_mm is not None:
                d_max_grid = min(d_max_grid, max_outer_diameter_mm / 2.0)
            for C_ in [x * 0.5 for x in range(6, 21)]:  # C: 3.0–10.0 step 0.5
                d_ub = d_max_grid
                for d_ in [x * 0.05 for x in range(16, int(d_ub / 0.05) + 1)]:
                    D_ = C_ * d_
                    if not _feasible(d_, D_):
                        continue
                    n = max(_active_coils(d_, D_), self.N_A_MIN)
                    vol = (math.pi**2 / 4.0) * d_**2 * D_ * n
                    if vol < best_vol:
                        best_vol = vol
                        best = (d_, D_, n)
            if best is not None:
                d, D, n_a = best
                result.success = True
                result.message = "Grid search fallback"
            else:
                raise ValueError(
                    "Cannot design an extension spring satisfying all constraints "
                    f"(F={load_force_n}N, δ={deflection_mm}mm, Fi={F_i}N). "
                    "Try relaxing OD/FL or increasing allowable stress (stronger material)."
                )

        C = D / d
        k_actual = _spring_rate(d, D, n_a, G)
        L0 = n_a * d + deflection_mm + 4.0 * D  # body length + 2 hook allowances
        pitch = d  # extension springs are typically close-wound (pitch ≈ d)

        sigma_hook, tau_body, Kw = _stresses(d, D)
        tau_hook = Kw * 8.0 * load_force_n * D / (math.pi * d**3)
        Ks_body = _wahl_correction(C)

        # Governing safety factor across the three failure modes checked
        # during optimization (hook bending, coil shear, hook torsion) —
        # the minimum is the true design margin.
        safety_hook = yield_strength_mpa / max(sigma_hook, 1e-9)
        safety_body_shear = (
            TORSIONAL_YIELD_FACTOR * yield_strength_mpa / max(tau_body, 1e-9)
        )
        safety_hook_torsion = (
            TORSIONAL_YIELD_FACTOR * yield_strength_mpa / max(tau_hook, 1e-9)
        )
        safety_shear = min(safety_hook, safety_body_shear, safety_hook_torsion)

        return {
            "wire_diameter_mm": round(d, 3),
            "mean_coil_diameter_mm": round(D, 3),
            "outer_diameter_mm": round(D + d, 3),
            "inner_diameter_mm": round(D - d, 3),
            "active_coils": round(n_a, 2),
            "total_coils": round(n_a, 2),  # no dead coils in extension springs
            "free_length_mm": round(L0, 3),
            "pitch_mm": round(pitch, 3),
            "spring_index": round(C, 3),
            "spring_rate_n_mm": round(k_actual, 4),
            "wahl_factor": round(Ks_body, 4),
            "corrected_shear_stress_mpa": round(tau_body, 3),
            "safety_factor_shear": round(safety_shear, 3),
            "slenderness_ratio": round(_slenderness_ratio(L0, D), 3),
            "torsion_moment_n_mm": None,
            "angular_deflection_deg": None,
            "hook_bending_stress_mpa": round(sigma_hook, 3),
            "hook_torsion_stress_mpa": round(tau_hook, 3),
            "initial_tension_n": round(F_i, 3),
            "arm_bending_stress_mpa": None,
            "coil_torsion_stress_mpa": None,
            "optimizer_converged": bool(result.success),
            "optimizer_message": result.message,
        }


# ─────────────────────────────────────────────────────────────────────────────
# TorsionEngine — angular deflection + arm bending
# ─────────────────────────────────────────────────────────────────────────────


class TorsionEngine(SpringDesignEngine):
    """
    Torsion spring engine. Design variables: wire diameter (d), mean coil
    diameter (D), and moment-arm length. Active coils derived from the
    target angular stiffness (torque/angle), following Shigley eq. 10-6.

    Primary failure mode: bending stress in the arms at the coil-arm
    junction (curved-beam bending, Bergsträsser-corrected). Secondary:
    torsional shear in the coil body from the applied moment (uncorrected,
    since torsion-spring coils see the load as a bending moment about the
    coil axis, not a direct shear force like compression/extension).

    Bounds: wire 1.0–8 mm, spring index C = D/d in [3, 10], active coils in
    [2, 40], moment-arm length in [10, 100] mm.
    """

    name = "torsion"

    D_INDEX_MIN = 3.0
    D_INDEX_MAX = 10.0
    N_A_MIN = 2.0
    N_A_MAX = 40.0
    D_WIRE_BOUNDS = (1.0, 8.0)
    ARM_LENGTH_BOUNDS = (10.0, 100.0)

    def compute_stress(self, d: float, D: float, n_a: float, **kwargs: Any) -> dict[str, Any]:
        M = kwargs["torsion_moment_n_mm"]
        C = D / d
        Kb = _bending_correction(C)
        sigma_arm = _arm_bending_stress(M, d, Kb)
        tau_coil = _coil_torsion_stress(M, d)
        return {"arm_bending_stress_mpa": sigma_arm, "coil_torsion_stress_mpa": tau_coil}

    def validate_constraints(self, d: float, D: float, n_a: float, **kwargs: Any) -> bool:
        if d <= 0 or D <= 0:
            return False
        if n_a < self.N_A_MIN or n_a > self.N_A_MAX:
            return False
        C = D / d
        return self.D_INDEX_MIN <= C <= self.D_INDEX_MAX

    def compute_geometry(
        self,
        *,
        load_force_n: float,
        deflection_mm: float,
        shear_modulus_gpa: float,
        yield_strength_mpa: float,
        max_outer_diameter_mm: float | None = None,
        max_free_length_mm: float | None = None,
        dead_coils: float = 0.0,
        cyclic_load: bool = False,
        arm_length_mm: float = 25.0,
        **kwargs: Any,
    ) -> dict[str, Any]:
        G = shear_modulus_gpa * GPa_TO_N_MM2
        E = 200.0 * GPa_TO_N_MM2  # approximate E for steel, N/mm² (bending modulus)
        k_target = load_force_n / deflection_mm

        # Applied torque from the tangential force acting at the moment arm.
        torsion_moment = load_force_n * arm_length_mm

        ALLOWABLE_BEND_MPA = yield_strength_mpa / GOODMAN_TARGET_SF
        ALLOWABLE_SHEAR_MPA = (
            TORSIONAL_YIELD_FACTOR * yield_strength_mpa / GOODMAN_TARGET_SF
        )
        PENALTY = 1e12

        def _active_coils(d: float, D: float) -> float:
            # Same linear spring-rate driver used by compression/extension —
            # keeps active-coil counts in a physically sane range independent
            # of the (much larger-scale) arm-moment stress checks below.
            # k = G·d⁴ / (8·D³·n_a)  →  n_a = G·d⁴ / (8·D³·k)
            return G * d**4 / (8.0 * D**3 * k_target)

        def _stresses(d: float, D: float) -> tuple[float, float]:
            C = D / d
            Kb = _bending_correction(C)
            sigma_arm = _arm_bending_stress(torsion_moment, d, Kb)
            tau_coil = _coil_torsion_stress(torsion_moment, d)
            return sigma_arm, tau_coil

        def _volume(x: np.ndarray) -> float:
            d, D_ = float(x[0]), float(x[1])
            if d <= 0 or D_ <= 0:
                return PENALTY
            n_a = _active_coils(d, D_)
            if n_a < self.N_A_MIN or n_a > self.N_A_MAX:
                return PENALTY
            C = D_ / d
            if C < self.D_INDEX_MIN or C > self.D_INDEX_MAX:
                return PENALTY
            sigma_arm, tau_coil = _stresses(d, D_)
            if sigma_arm > ALLOWABLE_BEND_MPA:
                return PENALTY
            if tau_coil > ALLOWABLE_SHEAR_MPA:
                return PENALTY
            if max_outer_diameter_mm is not None and (D_ + d) > max_outer_diameter_mm:
                return PENALTY
            n_t = n_a + dead_coils
            return (math.pi**2 / 4.0) * d**2 * D_ * n_t

        d_bounds = self.D_WIRE_BOUNDS
        D_max = 150.0
        if max_outer_diameter_mm is not None:
            D_max = max(4.0, max_outer_diameter_mm - 1.0)
        D_bounds = (4.0, D_max)

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
        n_a = max(_active_coils(d, D), self.N_A_MIN)

        def _feasible(d: float, D: float) -> bool:
            if d <= 0 or D <= 0:
                return False
            n = _active_coils(d, D)
            if n < self.N_A_MIN or n > self.N_A_MAX:
                return False
            C = D / d
            if C < self.D_INDEX_MIN or C > self.D_INDEX_MAX:
                return False
            sigma_arm, tau_coil = _stresses(d, D)
            if sigma_arm > ALLOWABLE_BEND_MPA:
                return False
            if tau_coil > ALLOWABLE_SHEAR_MPA:
                return False
            if max_outer_diameter_mm is not None and (D + d) > max_outer_diameter_mm + 0.01:
                return False
            return True

        if not _feasible(d, D):
            logger.warning(
                "Torsion DE opt infeasible (d=%.3f, D=%.3f) — running grid fallback.",
                d, D,
            )
            best = None
            best_vol = float("inf")
            d_max_grid = 8.0
            if max_outer_diameter_mm is not None:
                d_max_grid = min(d_max_grid, max_outer_diameter_mm / 2.0)
            for C_ in [x * 0.5 for x in range(6, 21)]:  # C: 3.0–10.0 step 0.5
                d_ub = d_max_grid
                for d_ in [x * 0.05 for x in range(20, int(d_ub / 0.05) + 1)]:
                    D_ = C_ * d_
                    if not _feasible(d_, D_):
                        continue
                    n = max(_active_coils(d_, D_), self.N_A_MIN)
                    n_t = n + dead_coils
                    vol = (math.pi**2 / 4.0) * d_**2 * D_ * n_t
                    if vol < best_vol:
                        best_vol = vol
                        best = (d_, D_, n)
            if best is not None:
                d, D, n_a = best
                result.success = True
                result.message = "Grid search fallback"
            else:
                raise ValueError(
                    "Cannot design a torsion spring satisfying all constraints "
                    f"(F={load_force_n}N, arm={arm_length_mm}mm, M={torsion_moment}N·mm). "
                    "Try relaxing OD or increasing allowable stress (stronger material)."
                )

        n_t = n_a + dead_coils
        C = D / d
        k_actual = _spring_rate(d, D, n_a, G)  # linear-equivalent rate, informational
        L0 = n_t * d  # axial body length (legs excluded)
        pitch = d

        angular_deflection = (
            10.8 * torsion_moment * D * n_a / (E * d**4)
        ) * (180.0 / math.pi)

        sigma_arm, tau_coil = _stresses(d, D)

        return {
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
            "wahl_factor": None,
            "corrected_shear_stress_mpa": None,
            "safety_factor_shear": round(
                ALLOWABLE_SHEAR_MPA * GOODMAN_TARGET_SF / max(tau_coil, 1e-9), 3
            ),
            "slenderness_ratio": round(_slenderness_ratio(L0, D), 3),
            "torsion_moment_n_mm": round(torsion_moment, 3),
            "angular_deflection_deg": round(angular_deflection, 3),
            "hook_bending_stress_mpa": None,
            "hook_torsion_stress_mpa": None,
            "initial_tension_n": None,
            "arm_bending_stress_mpa": round(sigma_arm, 3),
            "coil_torsion_stress_mpa": round(tau_coil, 3),
            "optimizer_converged": bool(result.success),
            "optimizer_message": result.message,
        }


_ENGINES: dict[str, SpringDesignEngine] = {
    "compression": CompressionEngine(),
    "extension": ExtensionEngine(),
    "torsion": TorsionEngine(),
}


def _get_engine(spring_type: str) -> SpringDesignEngine:
    """Route to the engine for ``spring_type``, defaulting to compression."""
    return _ENGINES.get(spring_type, _ENGINES["compression"])


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
    initial_tension_n: float = 0.0,
    arm_length_mm: float = 25.0,
) -> str:
    """
    Compute optimal helical spring geometry using scipy.optimize.differential_evolution.

    Dispatches to a per-``spring_type`` ``SpringDesignEngine`` (compression,
    extension, or torsion) that owns its own design variables, optimizer
    bounds, and stress model. Unknown spring types fall back to the
    compression engine to preserve legacy behavior.

    Args:
        spring_type:          One of 'compression', 'extension', 'torsion'.
        load_force_n:         Required operating load in Newtons.
        deflection_mm:        Required deflection at that load in mm.
        max_outer_diameter_mm: Hard OD constraint (mm). None = unconstrained.
        max_free_length_mm:   Hard free-length constraint (mm). None = unconstrained.
        shear_modulus_gpa:    G of the chosen material in GPa (default: 79.3 = steel).
        yield_strength_mpa:   Sy of the material in MPa (default: 1500).
        dead_coils:           Non-active (dead) coils at each end (default: 2.0 total,
                               compression only — extension/torsion ignore this).
        cyclic_load:          If True, rejects designs failing Goodman fatigue Sf ≥ 1.3
                               (compression only).
        initial_tension_n:    Extension-spring preload (N). Ignored by other types.
        arm_length_mm:        Torsion-spring moment-arm length (mm). Ignored by other types.

    Returns:
        JSON string with the computed geometry dict or an error message.
    """
    try:
        engine = _get_engine(spring_type)
        geometry = engine.compute_geometry(
            load_force_n=load_force_n,
            deflection_mm=deflection_mm,
            shear_modulus_gpa=shear_modulus_gpa,
            yield_strength_mpa=yield_strength_mpa,
            max_outer_diameter_mm=max_outer_diameter_mm,
            max_free_length_mm=max_free_length_mm,
            dead_coils=dead_coils,
            cyclic_load=cyclic_load,
            initial_tension_n=initial_tension_n,
            arm_length_mm=arm_length_mm,
        )
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
