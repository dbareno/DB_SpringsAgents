"""
app/tools/commercial.py
─────────────────────────────────────────────────────────────────────────────
LangChain @tool definition for commercial scoring and ranking of spring
proposals.

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
# Tool 5 — Commercial Scoring
# ─────────────────────────────────────────────────────────────────────────────


@tool
def commercial_scoring_tool(proposals: str) -> str:
    """
    Rank spring proposals by weighted commercial index with manufacturing costs.

    Computes true wire mass, material cost, plus manufacturing costs (winding,
    heat treatment, shot peening) and a fatigue-aware life estimate. The
    composite score balances total cost, durability, and safety.

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

    Returns:
        JSON string with ranked proposals list (includes manufacturing cost
        breakdown) and chart-ready data.
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

        # ── Manufacturing cost model ───────────────────────────────────────

        # 1. Winding cost: quadratic penalty for d/D away from sweet spot 0.08
        #    d/D < 0.04 → thin wire on large coil, hard to control pitch
        #    d/D > 0.15 → thick wire on small coil, high force + springback
        sweet_ratio = 0.08
        d_D = df["wire_diameter_mm"] / df["mean_coil_diameter_mm"]
        dev = (d_D - sweet_ratio) / sweet_ratio  # relative deviation
        winding_factor = (1.0 + 2.0 * dev ** 2).clip(upper=3.0)
        base_winding_usd = 0.08  # automated CNC winding at sweet spot
        df["winding_cost_usd"] = (base_winding_usd * winding_factor).round(4)

        # 2. Heat treatment
        #    - Stress relief after winding: always needed, small fixed cost
        #    - Quench & temper: needed when Sy > 1500 MPa (high-strength alloys)
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
        df["shot_peen_usd"] = cyclic.apply(
            lambda v: 0.12 if v else 0.0
        )

        # 4. Total manufacturing cost
        df["manufacturing_usd"] = (
            df["winding_cost_usd"]
            + df["heat_treat_usd"]
            + df["shot_peen_usd"]
        ).round(4)

        # 5. Total cost per spring
        df["total_cost_usd"] = (
            df["material_cost_usd"] + df["manufacturing_usd"]
        ).round(4)

        # ── Estimated life cycles (fatigue-aware) ──────────────────────────
        # Baseline: Sf × 500k. Shot peening improves life 2.5×.
        baseline_cycles = 500_000
        shot_peen_mult = cyclic.apply(lambda v: 2.5 if v else 1.0)
        df["estimated_life_cycles"] = (
            df["safety_factor_shear"] * baseline_cycles * shot_peen_mult
        ).astype(int)

        # ── Composite score  (higher = better) ────────────────────────────
        # Weights: total cost 35%, shear safety 25%, buckling safety 15%,
        #          life efficiency 15%, free-length compactness 10%
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

        # ── Chart-ready payload (Recharts / Victory format) ────────────────
        chart_data = df[[
            "proposal_id", "rank", "composite_score",
            "total_cost_usd", "material_cost_usd", "manufacturing_usd",
            "estimated_life_cycles",
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
                "manufacturing_usd": round(row["manufacturing_usd"], 4),
                "total_cost_usd": round(row["total_cost_usd"], 4),
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
