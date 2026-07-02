"""
initial_schema
──────────────
Create the initial database schema for Spring Design Agent.

Tables
  - spring_materials    — Catalogue of available spring alloys
  - design_projects     — Historical log of completed design runs
  - design_iterations   — Per-iteration snapshots (geometry + compliance)

Revision ID: initial_schema
Revises: None
Create Date: 2026-07-02
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── spring_materials ─────────────────────────────────────────────────────
    op.create_table(
        "spring_materials",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("standard", sa.String(60), nullable=False,
                  comment="e.g. 'ASTM A228', 'DIN 17223-C'"),
        sa.Column("shear_modulus_gpa", sa.Float(), nullable=False),
        sa.Column("elastic_modulus_gpa", sa.Float(), nullable=False),
        sa.Column("density_kg_m3", sa.Float(), nullable=False),
        sa.Column("yield_strength_mpa", sa.Float(), nullable=False),
        sa.Column("ultimate_strength_mpa", sa.Float(), nullable=False),
        sa.Column("max_temp_c", sa.Float(), nullable=False),
        sa.Column("corrosion_resistant", sa.Boolean(), default=False),
        sa.Column("cost_usd_per_kg", sa.Float(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    # ── design_projects ──────────────────────────────────────────────────────
    op.create_table(
        "design_projects",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.String(64), nullable=False),
        sa.Column("raw_user_input", sa.Text(), nullable=False),
        sa.Column("spring_type", sa.String(30), nullable=False, server_default="unknown"),
        sa.Column("status", sa.String(40), nullable=False, server_default="pending",
                  comment="pending | needs_clarification | approved | error | iteration_limit"),
        sa.Column("final_report", JSONB(), nullable=True),
        sa.Column("total_iterations", sa.Integer(), default=0),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_design_projects_session_id", "design_projects", ["session_id"])

    # ── design_iterations ────────────────────────────────────────────────────
    op.create_table(
        "design_iterations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.BigInteger(),
                  sa.ForeignKey("design_projects.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("material_id", sa.Integer(),
                  sa.ForeignKey("spring_materials.id"), nullable=True),
        sa.Column("iteration_number", sa.Integer(), nullable=False),
        sa.Column("geometry_snapshot", JSONB(), nullable=True),
        sa.Column("compliance_snapshot", JSONB(), nullable=True),
        sa.Column("approved", sa.Boolean(), default=False),
        sa.Column("failure_modes", JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_design_iterations_project_id",
        "design_iterations",
        ["project_id"],
    )


def downgrade() -> None:
    op.drop_table("design_iterations")
    op.drop_table("design_projects")
    op.drop_table("spring_materials")
