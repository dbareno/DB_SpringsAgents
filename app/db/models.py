"""
app/db/models.py
─────────────────────────────────────────────────────────────────────────────
SQLAlchemy ORM models for the Spring Design Agent system.

Tables
──────
  spring_materials     — Catalogue of available spring alloys.
  design_projects      — Historical log of completed design runs.
  design_iterations    — Per-iteration snapshots (geometry + compliance).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy import JSON
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""


class SpringMaterial(Base):
    """
    Catalogue of spring wire alloys and their mechanical properties.

    Populated once at startup via the seed script ``scripts/seed_materials.py``.
    Read-only during normal operation.
    """

    __tablename__ = "spring_materials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    standard: Mapped[str] = mapped_column(
        String(60), nullable=False, comment="e.g. 'ASTM A228', 'DIN 17223-C'"
    )
    shear_modulus_gpa: Mapped[float] = mapped_column(Float, nullable=False)
    elastic_modulus_gpa: Mapped[float] = mapped_column(Float, nullable=False)
    density_kg_m3: Mapped[float] = mapped_column(Float, nullable=False)
    yield_strength_mpa: Mapped[float] = mapped_column(Float, nullable=False)
    ultimate_strength_mpa: Mapped[float] = mapped_column(Float, nullable=False)
    max_temp_c: Mapped[float] = mapped_column(Float, nullable=False)
    corrosion_resistant: Mapped[bool] = mapped_column(Boolean, default=False)
    cost_usd_per_kg: Mapped[float] = mapped_column(Float, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Relationships
    iterations: Mapped[list["DesignIteration"]] = relationship(
        back_populates="material"
    )

    def __repr__(self) -> str:
        return f"<SpringMaterial id={self.id} name={self.name!r}>"


class DesignProject(Base):
    """
    Top-level record for a complete design workflow run.

    One record per API call to ``POST /api/v1/design``.
    """

    __tablename__ = "design_projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    raw_user_input: Mapped[str] = mapped_column(Text, nullable=False)
    spring_type: Mapped[str] = mapped_column(String(30), nullable=False, default="unknown")
    status: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default="pending",
        comment="pending | needs_clarification | approved | error | iteration_limit",
    )
    final_report: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    quote_snapshot: Mapped[dict | None] = mapped_column(
        JSON, nullable=True,
        comment="Cost parameters (setup_cost, margin, tiers) used at quote generation time for reproducibility"
    )
    total_iterations: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )

    # Relationships
    iterations: Mapped[list["DesignIteration"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<DesignProject id={self.id} status={self.status!r}>"


class DesignIteration(Base):
    """
    Per-iteration snapshot for a design project (one row per redesign cycle).
    """

    __tablename__ = "design_iterations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("design_projects.id", ondelete="CASCADE"), nullable=False
    )
    material_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("spring_materials.id"), nullable=True
    )
    iteration_number: Mapped[int] = mapped_column(Integer, nullable=False)
    geometry_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    compliance_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    approved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    failure_modes: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow
    )

    # Relationships
    project: Mapped["DesignProject"] = relationship(back_populates="iterations")
    material: Mapped["SpringMaterial | None"] = relationship(
        back_populates="iterations"
    )

    def __repr__(self) -> str:
        return (
            f"<DesignIteration project={self.project_id} "
            f"iter={self.iteration_number} approved={self.approved}>"
        )


class CommercialSettings(Base):
    """
    Admin-configurable cost model parameters for quotation.

    Defines:
    - Base setup/NRE cost amortized across lot size
    - Target margin percentage applied to unit cost
    - Price tier thresholds and their quantity breaks

    One row per "active" configuration; defaults provided at system startup.
    """

    __tablename__ = "commercial_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(
        String(100), nullable=False, default="default",
        comment="Configuration name; 'default' is used if not specified"
    )
    setup_cost_usd: Mapped[float] = mapped_column(
        Float, nullable=False, default=250.0,
        comment="Fixed engineering + NRE cost per order (amortized by lot size)"
    )
    margin_percent: Mapped[float] = mapped_column(
        Float, nullable=False, default=25.0,
        comment="Target profit margin as percentage (0-100)"
    )
    # Tier definitions: stored as JSON for flexibility
    # Format: [{"min_qty": 1, "max_qty": 10, "name": "Prototype"},
    #          {"min_qty": 11, "max_qty": 100, "name": "Small"},
    #          {"min_qty": 101, "max_qty": null, "name": "Production"}]
    tier_definitions: Mapped[list | None] = mapped_column(
        JSON, nullable=True,
        comment="List of qty tiers with min/max boundaries and display names"
    )
    active: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False,
        comment="Only active=True configs are used in quotation"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def __repr__(self) -> str:
        return (
            f"<CommercialSettings id={self.id} name={self.name!r} "
            f"margin={self.margin_percent}% setup=${self.setup_cost_usd}>"
        )
