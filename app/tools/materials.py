"""
app/tools/materials.py
─────────────────────────────────────────────────────────────────────────────
LangChain @tool definition for material property queries.

Each tool is a self-contained callable decorated with @tool so LangGraph's
ToolNode can invoke it automatically when an agent emits a ToolCall.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import pandas as pd
from langchain_core.tools import tool

logger = logging.getLogger(__name__)


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
    min_yield_strength_mpa: float | None = None,
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

        if min_yield_strength_mpa is not None:
            df = df[df["yield_strength_mpa"] >= min_yield_strength_mpa]

        if df.empty:
            missing = []
            if min_yield_strength_mpa:
                missing.append(f"Sy ≥ {min_yield_strength_mpa:.0f}MPa")
            return json.dumps({
                "status": "no_match",
                "message": (
                    f"No material satisfies all constraints "
                    f"({' + '.join(missing) if missing else 'filters'}). "
                    "Consider relaxing requirements."
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
