"""
app/tools/physics.py
─────────────────────────────────────────────────────────────────────────────
Shared numeric helpers and fatigue/strength constants for helical spring
mechanics.

These are the single source of truth for stress, spring-rate, and buckling
calculations used by BOTH the geometry optimizer (``geometry.py``) and the
compliance checker (``compliance.py``) so their Goodman criteria never
drift apart.

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

import math

GPa_TO_N_MM2 = 1_000.0  # 1 GPa = 1 000 N/mm²
MPa_TO_N_MM2 = 1.0       # 1 MPa = 1 N/mm²

# ── Shared fatigue / strength assumptions (single source of truth) ──────────
# Used by BOTH the geometry optimizer (Tool 1) and the compliance check
# (Tool 3) so their Goodman criteria never drift apart.
FATIGUE_MIN_LOAD_RATIO = 0.1       # F_min = 0.1 × F_max for cyclic loading
ULTIMATE_FROM_YIELD_FACTOR = 1.25  # Sut ≈ 1.25 × Sy for typical spring steel
SHEAR_ENDURANCE_FACTOR = 0.324     # Ses ≈ 0.324 × Sut (Zimmerli, steel wire)
TORSIONAL_YIELD_FACTOR = 0.45      # Ssy = 0.45 × Sy (torsional yield limit)
GOODMAN_TARGET_SF = 1.3            # Sf ≥ 1.3 per DIN 2095 / EN 13906-1


def _wahl_correction(C: float) -> float:
    """Wahl stress-correction factor Ks = (4C-1)/(4C-4) + 0.615/C."""
    return (4 * C - 1) / (4 * C - 4) + 0.615 / C


def _bending_correction(C: float) -> float:
    """
    Bergsträsser bending stress-correction factor Kb for curved-beam bending
    (used for extension-spring hook stress and torsion-spring arm stress).

    Kb = (4C² - C - 1) / (4C(C - 1))   [Shigley, curved beam in bending]
    """
    return (4 * C**2 - C - 1) / (4 * C * (C - 1))


def _torsion_correction(C: float) -> float:
    """
    Curved-beam torsional (shear) stress-correction factor Kw for a curved
    section under torsion — same form as the Wahl factor but applied to a
    different local radius ratio (e.g. hook bend radius at point B).

    Kw = (4C - 1) / (4C - 4)   [Shigley, curved beam in torsion]
    """
    return (4 * C - 1) / (4 * C - 4)


def _hook_bending_stress(F: float, d: float, D: float, Kb: float) -> float:
    """
    Extension-spring hook bending stress at point A [Shigley eq. 10-33]:

        σ_A = F · [Kb · 16D/(πd³) + 4/(πd²)]
    """
    return F * (Kb * 16.0 * D / (math.pi * d**3) + 4.0 / (math.pi * d**2))


def _hook_torsion_stress(F: float, d: float, D: float, Kw: float) -> float:
    """
    Extension-spring hook torsional shear stress at point B [Shigley eq. 10-34]:

        τ_B = Kw · 8FD / (πd³)
    """
    return Kw * 8.0 * F * D / (math.pi * d**3)


def _arm_bending_stress(M: float, d: float, Kb: float) -> float:
    """
    Torsion-spring arm bending stress at the coil-arm junction [Shigley eq.
    10-38, adapted]:

        σ = Kb · 32M / (πd³)
    """
    return Kb * 32.0 * M / (math.pi * d**3)


def _coil_torsion_stress(M: float, d: float) -> float:
    """
    Torsion-spring secondary coil shear stress from the applied moment
    (uncorrected torsional shear on the wire cross-section):

        τ = 16M / (πd³)
    """
    return 16.0 * M / (math.pi * d**3)


def _spring_rate(d: float, D: float, n_a: float, G_n_mm2: float) -> float:
    """Helical spring rate k = G*d⁴ / (8*D³*n_a)  [N/mm]."""
    return (G_n_mm2 * d**4) / (8.0 * D**3 * n_a)


def _shear_stress(F: float, d: float, D: float) -> float:
    """Uncorrected shear stress τ = 8FD / (πd³)  [N/mm² = MPa]."""
    return (8.0 * F * D) / (math.pi * d**3)


def _slenderness_ratio(L0: float, D: float) -> float:
    """Slenderness ratio λ = L0/D (pandeo check)."""
    return L0 / D
