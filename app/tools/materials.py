"""
app/tools/materials.py
─────────────────────────────────────────────────────────────────────────────
LangChain @tool definition for material property queries.

Each tool is a self-contained callable decorated with @tool so LangGraph's
ToolNode can invoke it automatically when an agent emits a ToolCall.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import pandas as pd
from langchain_core.tools import tool

from app.db.repositories.material_repository import MaterialRepository
from app.db.session import db_session

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
    Query the ``spring_materials`` table and return the best-matching
    materials ranked by suitability.

    Reads live catalogue data via ``MaterialRepository`` — prices and
    availability update without a code change or restart. Only ``active``
    (non soft-deleted) materials are considered.

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
        rows = _fetch_materials_sync(
            min_operating_temperature_c=operating_temperature_c,
            corrosion_resistant=corrosion_resistant if corrosion_resistant else None,
            cyclic_load=cyclic_load,
            max_cost_usd_per_kg=max_cost_usd_per_kg,
            min_yield_strength_mpa=min_yield_strength_mpa,
        )

        if not rows:
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
                "closest": [],
            })

        df = pd.DataFrame(rows)

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


async def _fetch_materials(
    *,
    min_operating_temperature_c: float | None,
    corrosion_resistant: bool | None,
    cyclic_load: bool,
    max_cost_usd_per_kg: float | None,
    min_yield_strength_mpa: float | None,
) -> list[dict[str, Any]]:
    """Query ``spring_materials`` via ``MaterialRepository`` and shape rows.

    Returns a list of plain dicts matching the tool's historical stub shape
    (``material_id`` instead of the ORM's ``id``), so downstream scoring and
    Agent 3's consumption logic need no changes.
    """
    async with db_session() as session:
        repo = MaterialRepository(session)
        materials = await repo.list_filtered(
            min_operating_temperature_c=min_operating_temperature_c,
            corrosion_resistant=corrosion_resistant,
            cyclic_load=cyclic_load,
            max_cost_usd_per_kg=max_cost_usd_per_kg,
            min_yield_strength_mpa=min_yield_strength_mpa,
            active_only=True,
        )
        return [
            {
                "material_id": m.id,
                "name": m.name,
                "shear_modulus_gpa": m.shear_modulus_gpa,
                "elastic_modulus_gpa": m.elastic_modulus_gpa,
                "density_kg_m3": m.density_kg_m3,
                "yield_strength_mpa": m.yield_strength_mpa,
                "ultimate_strength_mpa": m.ultimate_strength_mpa,
                "max_temp_c": m.max_temp_c,
                "corrosion_resistant": m.corrosion_resistant,
                "cost_usd_per_kg": m.cost_usd_per_kg,
            }
            for m in materials
        ]


def _fetch_materials_sync(
    *,
    min_operating_temperature_c: float | None,
    corrosion_resistant: bool | None,
    cyclic_load: bool,
    max_cost_usd_per_kg: float | None,
    min_yield_strength_mpa: float | None,
) -> list[dict[str, Any]]:
    """Sync bridge for ``_fetch_materials``.

    ``query_material_properties_tool`` is a LangChain ``@tool`` invoked
    synchronously (``.invoke(...)``) by the agents, while the DB layer is
    fully async. ``asyncio.run`` is safe here because this tool is never
    called from within a running event loop (LangGraph node execution for
    this tool is synchronous).
    """
    return asyncio.run(
        _fetch_materials(
            min_operating_temperature_c=min_operating_temperature_c,
            corrosion_resistant=corrosion_resistant,
            cyclic_load=cyclic_load,
            max_cost_usd_per_kg=max_cost_usd_per_kg,
            min_yield_strength_mpa=min_yield_strength_mpa,
        )
    )
