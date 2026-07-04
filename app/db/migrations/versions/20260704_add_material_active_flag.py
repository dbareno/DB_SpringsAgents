"""
add_material_active_flag
─────────────────────────
Add a soft-delete ``active`` flag to ``spring_materials``.

Retiring a material must never hard-delete the row (design_iterations.material_id
holds a historical foreign key to it). Deactivation is a boolean flip instead.

Revision ID: add_material_active_flag
Revises: initial_schema
Create Date: 2026-07-04
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "add_material_active_flag"
down_revision: Union[str, None] = "initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "spring_materials",
        sa.Column(
            "active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )


def downgrade() -> None:
    op.drop_column("spring_materials", "active")
