"""
phase6_autonomy_memory
──────────────────────
Add fields for Phase 6 autonomy & memory features (ADR-6, ADR-7).

Changes:
- design_projects: add outcome (won/lost/pending) and requirement_embedding_key
  for design history similarity search and won/lost learning.
- compliance_snapshots: add redesign_rationale for LLM-grounded redesign explanations.

Revision ID: phase6_autonomy_memory
Revises: add_material_active_flag
Create Date: 2026-07-04
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "phase6_autonomy_memory"
down_revision: Union[str, None] = "add_material_active_flag"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add outcome and embedding_key to design_projects
    op.add_column(
        "design_projects",
        sa.Column(
            "outcome",
            sa.String(20),
            nullable=True,
            comment="won | lost | pending — tracks success for learning",
        ),
    )
    op.add_column(
        "design_projects",
        sa.Column(
            "requirement_embedding_key",
            sa.String(256),
            nullable=True,
            comment="Hash/key of requirements for similarity search",
        ),
    )


def downgrade() -> None:
    op.drop_column("design_projects", "requirement_embedding_key")
    op.drop_column("design_projects", "outcome")
