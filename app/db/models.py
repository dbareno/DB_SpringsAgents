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
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
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

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    raw_user_input: Mapped[str] = mapped_column(Text, nullable=False)
    spring_type: Mapped[str] = mapped_column(String(30), nullable=False, default="unknown")
    status: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default="pending",
        comment="pending | needs_clarification | approved | error | iteration_limit",
    )
    final_report: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    total_iterations: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
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

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("design_projects.id", ondelete="CASCADE"), nullable=False
    )
    material_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("spring_materials.id"), nullable=True
    )
    iteration_number: Mapped[int] = mapped_column(Integer, nullable=False)
    geometry_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    compliance_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    failure_modes: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
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
