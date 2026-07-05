"""
app/tools/commercial.py
─────────────────────────────────────────────────────────────────────────────
LangChain @tool definition for commercial scoring and ranking of spring
proposals, including lot-size amortization, margin modeling, and price tiers.

Each tool is a self-contained callable decorated with @tool so LangGraph's
ToolNode can invoke it automatically when an agent emits a ToolCall.
"""

from __future__ import annotations

import json
import logging
import math
from typing import Any

import pandas as pd
from langchain_core.tools import tool

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Tier definitions: default pricing tiers by lot size
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_TIER_DEFINITIONS = [
    {"min_qty": 1, "max_qty": 10, "name": "Prototype (1-10)"},
    {"min_qty": 11, "max_qty": 100, "name": "Small Batch (11-100)"},
    {"min_qty": 101, "max_qty": None, "name": "Production (101+)"},
]

DEFAULT_SETUP_COST_USD = 250.0
DEFAULT_MARGIN_PERCENT = 25.0


def _compute_price_tiers(
    total_unit_cost_usd: float,
    margin_percent: float,
    tier_definitions: list[dict] | None = None,
) -> list[dict]:
    """
    Compute unit prices across quantity tiers with setup amortization.

    For each tier, computes:
        unit_price = (total_unit_cost + setup_cost / qty) * (1 + margin_percent / 100)

    Returns a list of {qty_min, qty_max, qty_example, unit_price, tier_name}.
    """
    if tier_definitions is None:
        tier_definitions = DEFAULT_TIER_DEFINITIONS

    setup_cost = DEFAULT_SETUP_COST_USD
    tiers = []

    for tier_def in tier_definitions:
        min_qty = tier_def.get("min_qty", 1)
        max_qty = tier_def.get("max_qty")  # None means "unlimited"
        tier_name = tier_def.get("name", f"{min_qty}+")

        # Use the minimum quantity for the tier to compute price
        # (smaller qty → higher amortized setup cost → higher price)
        qty_example = min_qty
        amortized_setup = setup_cost / qty_example if qty_example > 0 else 0.0
        unit_cost_with_setup = total_unit_cost_usd + amortized_setup
        unit_price = unit_cost_with_setup * (1.0 + margin_percent / 100.0)

        tiers.append({
            "qty_min": min_qty,
            "qty_max": max_qty,
            "qty_example": qty_example,
            "unit_price_usd": round(unit_price, 4),
            "tier_name": tier_name,
        })

    return tiers


# ─────────────────────────────────────────────────────────────────────────────
# Tool 5 — Commercial Scoring (Phase 5: with lot amortization & margin)
# ─────────────────────────────────────────────────────────────────────────────


@tool
def commercial_scoring_tool(
    proposals: str,
    margin_percent: float | None = None,
    setup_cost_usd: float | None = None,
    tier_definitions: str | None = None,
) -> str:
    """
    Rank spring proposals by weighted commercial index with lot amortization & pricing tiers.

    Computes true wire mass, material cost, plus manufacturing costs (winding,
    heat treatment, shot peening) and a fatigue-aware life estimate. Extends
    with lot-size amortization of setup costs and margin-based unit pricing
    across multiple quantity tiers.

    Args:
        proposals: JSON string representing a list of proposal dicts.
                   Each dict must contain at minimum:
                   - ``proposal_id``              (str)
                   - ``wire_diameter_mm``         (float)
                   - ``mean_coil_diameter_mm``    (float)
                   - ``active_coils``             (float)
                   - ``total_coils``              (float)
                   - ``free_length_mm``           (float)
                   - ``outer_diameter_mm``        (float)
                   - ``density_kg_m3``            (float)
                   - ``cost_usd_per_kg``          (float)
                   - ``safety_factor_shear``      (float)
                   - ``safety_factor_buckling``   (float)
                   - ``cycles_expected``          (int, optional)
                   - ``yield_strength_mpa``       (float, optional)
                   - ``cyclic_load``              (bool, optional)
        margin_percent: Target margin as percentage (default 25.0).
        setup_cost_usd: Fixed setup/NRE cost per order (default 250.0).
        tier_definitions: JSON string of tier definition list (default: 3 tiers).

    Returns:
        JSON string with ranked proposals list (includes manufacturing cost
        breakdown and per-tier pricing) and chart-ready data.
    """
    try:
        # Use defaults if not provided
        if margin_percent is None:
            margin_percent = DEFAULT_MARGIN_PERCENT
        if setup_cost_usd is None:
            setup_cost_usd = DEFAULT_SETUP_COST_USD
        if tier_definitions is None:
            tier_defs = DEFAULT_TIER_DEFINITIONS
        else:
            tier_defs = json.loads(tier_definitions)

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

        # ── Manufacturing cost model ───────────────────────────────────────

        # 1. Winding cost: quadratic penalty for d/D away from sweet spot 0.08
        sweet_ratio = 0.08
        d_D = df["wire_diameter_mm"] / df["mean_coil_diameter_mm"]
        dev = (d_D - sweet_ratio) / sweet_ratio  # relative deviation
        winding_factor = (1.0 + 2.0 * dev ** 2).clip(upper=3.0)
        base_winding_usd = 0.08  # automated CNC winding at sweet spot
        df["winding_cost_usd"] = (base_winding_usd * winding_factor).round(4)

        # 2. Heat treatment
        df["stress_relief_usd"] = 0.03
        sy = df.get("yield_strength_mpa", pd.Series([0.0] * len(df)))
        df["quench_temper_usd"] = sy.apply(
            lambda v: 0.08 if (pd.notna(v) and v > 1500.0) else 0.0
        )
        df["heat_treat_usd"] = (
            df["stress_relief_usd"] + df["quench_temper_usd"]
        ).round(4)

        # 3. Shot peening (improves fatigue life 2-3x for cyclic loads)
        cyclic = df.get("cyclic_load", pd.Series([False] * len(df)))
        df["shot_peen_usd"] = cyclic.apply(lambda v: 0.12 if v else 0.0)

        # 4. Total manufacturing cost (no setup amortization yet)
        df["manufacturing_usd"] = (
            df["winding_cost_usd"]
            + df["heat_treat_usd"]
            + df["shot_peen_usd"]
        ).round(4)

        # 5. Total cost per spring (material + manufacturing, before setup/margin)
        df["total_cost_usd"] = (
            df["material_cost_usd"] + df["manufacturing_usd"]
        ).round(4)

        # ── Phase 5: Total unit cost with setup amortization (at qty=1) ────
        # This is used for the primary cost component; margin and tiers apply later
        df["total_unit_cost_usd"] = (
            df["material_cost_usd"] + df["manufacturing_usd"]
        ).round(4)

        # ── Estimated life cycles (fatigue-aware) ──────────────────────────
        baseline_cycles = 500_000
        shot_peen_mult = cyclic.apply(lambda v: 2.5 if v else 1.0)
        df["estimated_life_cycles"] = (
            df["safety_factor_shear"] * baseline_cycles * shot_peen_mult
        ).astype(int)

        # ── Composite score  (higher = better) ────────────────────────────
        # Uses material + manufacturing cost (before amortization)
        # so the score is consistent regardless of lot size
        max_cost = df["total_cost_usd"].max()
        max_sf = df["safety_factor_shear"].max()
        max_sfb = df["safety_factor_buckling"].max()
        max_life = df["estimated_life_cycles"].max()
        max_L0 = df["free_length_mm"].max()

        df["score_cost"] = 1.0 - (df["total_cost_usd"] / max_cost)
        df["score_shear"] = df["safety_factor_shear"] / max_sf
        df["score_buckling"] = df["safety_factor_buckling"] / max_sfb
        df["score_life"] = df["estimated_life_cycles"] / max_life
        df["score_compactness"] = 1.0 - (df["free_length_mm"] / max_L0)

        df["composite_score"] = (
            0.35 * df["score_cost"]
            + 0.25 * df["score_shear"]
            + 0.15 * df["score_buckling"]
            + 0.15 * df["score_life"]
            + 0.10 * df["score_compactness"]
        )

        df = df.sort_values("composite_score", ascending=False).reset_index(drop=True)
        df["rank"] = df.index + 1

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

        # ── Build ranked proposals with pricing tiers ──────────────────────
        ranked_proposals = []
        for _, row in df.iterrows():
            # Compute pricing tiers for this proposal
            price_tiers = _compute_price_tiers(
                float(row["total_unit_cost_usd"]),
                margin_percent,
                tier_defs,
            )

            ranked_proposals.append({
                "proposal_id": row["proposal_id"],
                "rank": int(row["rank"]),
                "composite_score": round(row["composite_score"], 4),
                "wire_mass_kg": round(row["wire_mass_kg"], 6),
                "material_cost_usd": round(row["material_cost_usd"], 4),
                "manufacturing_usd": round(row["manufacturing_usd"], 4),
                "total_cost_usd": round(row["total_cost_usd"], 4),
                "total_unit_cost_usd": round(row["total_unit_cost_usd"], 4),
                "margin_percent": margin_percent,
                "price_tiers": price_tiers,
                "estimated_life_cycles": int(row["estimated_life_cycles"]),
                "three_js_params": three_js_params(row),
            })

        # ── Chart-ready payload (Recharts / Victory format) ────────────────
        chart_data = df[[
            "proposal_id", "rank", "composite_score",
            "total_cost_usd", "material_cost_usd", "manufacturing_usd",
            "estimated_life_cycles",
            "safety_factor_shear", "safety_factor_buckling",
            "wire_mass_kg",
        ]].round(4).to_dict(orient="records")

        return json.dumps({
            "status": "ok",
            "ranked_proposals": ranked_proposals,
            "chart_data": chart_data,
            "cost_parameters": {
                "setup_cost_usd": setup_cost_usd,
                "margin_percent": margin_percent,
                "tier_definitions": tier_defs,
            },
        })

    except Exception as exc:
        logger.exception("commercial_scoring_tool failed")
        return json.dumps({"status": "error", "message": str(exc)})
