"""
app/api/v1/materials.py
─────────────────────────────────────────────────────────────────────────────
FastAPI router for materials catalogue admin endpoints.

Endpoints
─────────
  GET    /api/v1/materials/          → List materials (filterable).
  GET    /api/v1/materials/{id}      → Retrieve one material.
  POST   /api/v1/materials/          → Create a material (admin only).
  PUT    /api/v1/materials/{id}      → Update a material, incl. soft-delete
                                        via ``active: false`` (admin only).
  POST   /api/v1/materials/import    → Bulk CSV upsert (admin only).

Auth
────
Admin write endpoints (POST/PUT/import) are gated behind a hardcoded header
check (``X-Admin-Token``). This is a stub — real authentication/authorization
lands in Phase 6. Read endpoints (GET) are unauthenticated.
"""

from __future__ import annotations

import csv
import io
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.material_repository import MaterialRepository
from app.db.session import get_db_session
from app.schemas.material import (
    MaterialCreate,
    MaterialImportResponse,
    MaterialImportRowResult,
    MaterialListResponse,
    MaterialResponse,
    MaterialUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/materials", tags=["Materials Admin"])

# ── Admin auth stub ─────────────────────────────────────────────────────────
# TODO(Phase 6): replace with real authentication/authorization.
_ADMIN_TOKEN = "dev-admin-token"

_CSV_REQUIRED_COLUMNS = {
    "name",
    "standard",
    "shear_modulus_gpa",
    "elastic_modulus_gpa",
    "density_kg_m3",
    "yield_strength_mpa",
    "ultimate_strength_mpa",
    "max_temp_c",
    "cost_usd_per_kg",
}
_CSV_FLOAT_COLUMNS = (
    "shear_modulus_gpa",
    "elastic_modulus_gpa",
    "density_kg_m3",
    "yield_strength_mpa",
    "ultimate_strength_mpa",
    "max_temp_c",
    "cost_usd_per_kg",
)
_CSV_BOOL_COLUMNS = ("corrosion_resistant", "active")


def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    """Stub admin-auth dependency — validates a hardcoded header token."""
    if x_admin_token != _ADMIN_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid X-Admin-Token header.",
        )


def _parse_bool(value: str | None) -> bool | None:
    if value is None or value == "":
        return None
    return value.strip().lower() in {"1", "true", "yes", "y"}


# ─────────────────────────────────────────────────────────────────────────────
# Read endpoints
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/",
    response_model=MaterialListResponse,
    status_code=status.HTTP_200_OK,
    summary="List materials with optional engineering filters",
)
async def list_materials(
    active: bool | None = None,
    corrosion_resistant: bool | None = None,
    min_temp_c: float | None = None,
    max_cost_usd_per_kg: float | None = None,
    min_yield_strength_mpa: float | None = None,
    db: AsyncSession = Depends(get_db_session),
) -> MaterialListResponse:
    """
    List materials in the catalogue.

    By default only active materials are returned. Pass ``active=false`` to
    include soft-deleted materials, or omit filters to list everything active.
    """
    repo = MaterialRepository(db)
    materials = await repo.list_filtered(
        min_operating_temperature_c=min_temp_c,
        corrosion_resistant=corrosion_resistant,
        max_cost_usd_per_kg=max_cost_usd_per_kg,
        min_yield_strength_mpa=min_yield_strength_mpa,
        active_only=active if active is not None else True,
    )
    return MaterialListResponse(
        materials=[MaterialResponse.model_validate(m) for m in materials],
        count=len(materials),
    )


@router.get(
    "/{material_id}",
    response_model=MaterialResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve a single material by ID",
)
async def get_material(
    material_id: int,
    db: AsyncSession = Depends(get_db_session),
) -> MaterialResponse:
    repo = MaterialRepository(db)
    material = await repo.get_by_id(material_id)
    if material is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Material '{material_id}' not found.",
        )
    return MaterialResponse.model_validate(material)


# ─────────────────────────────────────────────────────────────────────────────
# Write endpoints (admin only)
# ─────────────────────────────────────────────────────────────────────────────


@router.post(
    "/",
    response_model=MaterialResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new material (admin only)",
    dependencies=[Depends(require_admin)],
)
async def create_material(
    payload: MaterialCreate,
    db: AsyncSession = Depends(get_db_session),
) -> MaterialResponse:
    repo = MaterialRepository(db)
    existing = await repo.get_by_name(payload.name)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Material '{payload.name}' already exists.",
        )
    material = await repo.create(payload.model_dump())
    return MaterialResponse.model_validate(material)


@router.put(
    "/{material_id}",
    response_model=MaterialResponse,
    status_code=status.HTTP_200_OK,
    summary="Update a material, including price changes and soft-delete (admin only)",
    dependencies=[Depends(require_admin)],
)
async def update_material(
    material_id: int,
    payload: MaterialUpdate,
    db: AsyncSession = Depends(get_db_session),
) -> MaterialResponse:
    repo = MaterialRepository(db)
    material = await repo.update(
        material_id, payload.model_dump(exclude_unset=True)
    )
    if material is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Material '{material_id}' not found.",
        )
    return MaterialResponse.model_validate(material)


# ─────────────────────────────────────────────────────────────────────────────
# CSV bulk import (admin only)
# ─────────────────────────────────────────────────────────────────────────────


@router.post(
    "/import",
    response_model=MaterialImportResponse,
    status_code=status.HTTP_200_OK,
    summary="Bulk create/update materials from a CSV file (admin only)",
    dependencies=[Depends(require_admin)],
)
async def import_materials_csv(
    file: UploadFile,
    db: AsyncSession = Depends(get_db_session),
) -> MaterialImportResponse:
    """
    Accepts a CSV with columns matching ``MaterialBase`` fields plus
    ``active`` (optional). Upserts by ``name`` — existing materials are
    updated, new ones are created. Malformed rows are reported individually
    without aborting the whole import.
    """
    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"CSV must be UTF-8 encoded: {exc}",
        ) from exc

    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="CSV file has no header row.",
        )

    missing_cols = _CSV_REQUIRED_COLUMNS - set(reader.fieldnames)
    if missing_cols:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"CSV missing required columns: {sorted(missing_cols)}",
        )

    repo = MaterialRepository(db)
    results: list[MaterialImportRowResult] = []
    created = updated = errors = 0

    for row_number, row in enumerate(reader, start=2):  # header is row 1
        name = (row.get("name") or "").strip()
        try:
            if not name:
                raise ValueError("Missing required field 'name'.")

            data: dict[str, object] = {"name": name, "standard": row.get("standard", "").strip()}
            for col in _CSV_FLOAT_COLUMNS:
                raw_val = row.get(col)
                if raw_val in (None, ""):
                    raise ValueError(f"Missing required numeric field '{col}'.")
                data[col] = float(raw_val)
            for col in _CSV_BOOL_COLUMNS:
                parsed = _parse_bool(row.get(col))
                if parsed is not None:
                    data[col] = parsed
            notes = row.get("notes")
            if notes:
                data["notes"] = notes

            existing = await repo.get_by_name(name)
            if existing is not None:
                await repo.update(existing.id, data)
                updated += 1
                results.append(
                    MaterialImportRowResult(row=row_number, name=name, action="updated")
                )
            else:
                data.setdefault("corrosion_resistant", False)
                data.setdefault("active", True)
                await repo.create(data)
                created += 1
                results.append(
                    MaterialImportRowResult(row=row_number, name=name, action="created")
                )
        except Exception as exc:  # noqa: BLE001 — per-row isolation is intentional
            errors += 1
            logger.warning("[materials/import] Row %d failed: %s", row_number, exc)
            results.append(
                MaterialImportRowResult(
                    row=row_number, name=name or None, action="error", error=str(exc)
                )
            )

    return MaterialImportResponse(
        total_rows=len(results),
        created=created,
        updated=updated,
        errors=errors,
        results=results,
    )
