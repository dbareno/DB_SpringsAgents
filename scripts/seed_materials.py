"""
scripts/seed_materials.py
─────────────────────────────────────────────────────────────────────────────
One-time seeder that populates the ``spring_materials`` table with a standard
catalogue of spring wire alloys.

Run with:
    python -m scripts.seed_materials
"""

from __future__ import annotations

import asyncio
import logging
import sys

# Ensure project root is on path when running as a script
sys.path.insert(0, ".")

from app.db.models import Base
from app.db.repositories.material_repository import MaterialRepository
from app.db.session import db_session, get_engine

logger = logging.getLogger(__name__)

MATERIALS_SEED = [
    {
        "name": "ASTM A228 Music Wire",
        "standard": "ASTM A228",
        "shear_modulus_gpa": 81.5,
        "elastic_modulus_gpa": 207.0,
        "density_kg_m3": 7850.0,
        "yield_strength_mpa": 1580.0,
        "ultimate_strength_mpa": 1900.0,
        "max_temp_c": 120.0,
        "corrosion_resistant": False,
        "cost_usd_per_kg": 3.80,
        "notes": "Highest quality carbon steel. Best for high-stress static applications.",
    },
    {
        "name": "ASTM A227 Hard-Drawn Wire",
        "standard": "ASTM A227",
        "shear_modulus_gpa": 79.3,
        "elastic_modulus_gpa": 200.0,
        "density_kg_m3": 7850.0,
        "yield_strength_mpa": 1100.0,
        "ultimate_strength_mpa": 1380.0,
        "max_temp_c": 120.0,
        "corrosion_resistant": False,
        "cost_usd_per_kg": 2.10,
        "notes": "General-purpose, low-cost. Not suitable for shock or impact loads.",
    },
    {
        "name": "ASTM A313 Type 302 Stainless Steel",
        "standard": "ASTM A313",
        "shear_modulus_gpa": 69.0,
        "elastic_modulus_gpa": 193.0,
        "density_kg_m3": 7920.0,
        "yield_strength_mpa": 1100.0,
        "ultimate_strength_mpa": 1380.0,
        "max_temp_c": 260.0,
        "corrosion_resistant": True,
        "cost_usd_per_kg": 9.50,
        "notes": "Excellent corrosion resistance. Food-grade and medical applications.",
    },
    {
        "name": "ASTM B197 Phosphor Bronze",
        "standard": "ASTM B197",
        "shear_modulus_gpa": 41.4,
        "elastic_modulus_gpa": 103.0,
        "density_kg_m3": 8860.0,
        "yield_strength_mpa": 510.0,
        "ultimate_strength_mpa": 640.0,
        "max_temp_c": 95.0,
        "corrosion_resistant": True,
        "cost_usd_per_kg": 14.20,
        "notes": "Good conductivity and corrosion resistance. Electrical/marine use.",
    },
    {
        "name": "ASTM A401 Chrome-Silicon (SAE 9254)",
        "standard": "ASTM A401",
        "shear_modulus_gpa": 77.2,
        "elastic_modulus_gpa": 200.0,
        "density_kg_m3": 7850.0,
        "yield_strength_mpa": 1720.0,
        "ultimate_strength_mpa": 2000.0,
        "max_temp_c": 245.0,
        "corrosion_resistant": False,
        "cost_usd_per_kg": 5.60,
        "notes": "Excellent for high-temp and high-cycle fatigue. Valve springs.",
    },
    {
        "name": "DIN 17223-C Chrome-Vanadium (VD-SiCr)",
        "standard": "DIN 17223",
        "shear_modulus_gpa": 78.5,
        "elastic_modulus_gpa": 206.0,
        "density_kg_m3": 7850.0,
        "yield_strength_mpa": 1650.0,
        "ultimate_strength_mpa": 1950.0,
        "max_temp_c": 220.0,
        "corrosion_resistant": False,
        "cost_usd_per_kg": 6.90,
        "notes": "Good fatigue and impact resistance. Automotive suspensions.",
    },
    {
        "name": "Inconel 718 (High-Temp Alloy)",
        "standard": "AMS 5596",
        "shear_modulus_gpa": 77.0,
        "elastic_modulus_gpa": 200.0,
        "density_kg_m3": 8190.0,
        "yield_strength_mpa": 1100.0,
        "ultimate_strength_mpa": 1380.0,
        "max_temp_c": 590.0,
        "corrosion_resistant": True,
        "cost_usd_per_kg": 95.00,
        "notes": "Aerospace / high-temperature environments. Very high cost.",
    },
]


async def seed() -> int:
    """Idempotently upsert the seed catalogue into ``spring_materials``.

    Returns the number of rows inserted (existing materials are skipped,
    never overwritten, so re-running is always safe).
    """
    # Create tables if they don't exist
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("[OK] Tables created / verified.")

    inserted = 0
    async with db_session() as session:
        repo = MaterialRepository(session)
        for mat_data in MATERIALS_SEED:
            # Skip if already present (idempotent — matched by unique name)
            existing = await repo.get_by_name(mat_data["name"])
            if existing is not None:
                print(f"  - Skipping (already exists): {mat_data['name']}")
                continue

            await repo.create(mat_data)
            inserted += 1
            print(f"  + Inserting: {mat_data['name']}")

    print(f"[OK] Materials seeded successfully ({inserted} inserted).")
    return inserted


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(seed())
