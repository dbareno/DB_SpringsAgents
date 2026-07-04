"""
app/schemas/material.py
─────────────────────────────────────────────────────────────────────────────
Pydantic request/response models for the materials admin API
(``/api/v1/materials``) and the CSV bulk-import endpoint.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class MaterialBase(BaseModel):
    """Shared fields for a spring material."""

    name: str = Field(..., min_length=1, max_length=120)
    standard: str = Field(..., min_length=1, max_length=60)
    shear_modulus_gpa: float = Field(..., gt=0)
    elastic_modulus_gpa: float = Field(..., gt=0)
    density_kg_m3: float = Field(..., gt=0)
    yield_strength_mpa: float = Field(..., gt=0)
    ultimate_strength_mpa: float = Field(..., gt=0)
    max_temp_c: float
    corrosion_resistant: bool = False
    cost_usd_per_kg: float = Field(..., gt=0)
    notes: str | None = None


class MaterialCreate(MaterialBase):
    """Payload for ``POST /api/v1/materials/``."""

    active: bool = True


class MaterialUpdate(BaseModel):
    """
    Payload for ``PUT /api/v1/materials/{id}``.

    All fields are optional — only provided fields are updated. Used for
    partial updates such as a price change or soft-delete (``active: false``).
    """

    name: str | None = Field(None, min_length=1, max_length=120)
    standard: str | None = Field(None, min_length=1, max_length=60)
    shear_modulus_gpa: float | None = Field(None, gt=0)
    elastic_modulus_gpa: float | None = Field(None, gt=0)
    density_kg_m3: float | None = Field(None, gt=0)
    yield_strength_mpa: float | None = Field(None, gt=0)
    ultimate_strength_mpa: float | None = Field(None, gt=0)
    max_temp_c: float | None = None
    corrosion_resistant: bool | None = None
    cost_usd_per_kg: float | None = Field(None, gt=0)
    notes: str | None = None
    active: bool | None = None


class MaterialResponse(MaterialBase):
    """Response shape for a single material."""

    id: int
    active: bool

    model_config = {"from_attributes": True}


class MaterialListResponse(BaseModel):
    """Response for ``GET /api/v1/materials/``."""

    materials: list[MaterialResponse]
    count: int


class MaterialImportRowResult(BaseModel):
    """Per-row outcome for the CSV import endpoint."""

    row: int
    name: str | None = None
    action: str = Field(description="'created', 'updated', or 'error'")
    error: str | None = None


class MaterialImportResponse(BaseModel):
    """Response for ``POST /api/v1/materials/import``."""

    total_rows: int
    created: int
    updated: int
    errors: int
    results: list[MaterialImportRowResult]
