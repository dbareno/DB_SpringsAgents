"""
app/db/repositories/material_repository.py
─────────────────────────────────────────────────────────────────────────────
Repositorio para la entidad SpringMaterial.

Encapsula todas las consultas a la tabla ``spring_materials``.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import SpringMaterial


class MaterialRepository:
    """
    Repositorio para operaciones CRUD sobre materiales de resortes.

    Recibe una sesión asíncrona de SQLAlchemy en el constructor.
    """

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get_by_id(self, material_id: int) -> SpringMaterial | None:
        """
        Retorna un material por su ID primario.

        Args:
            material_id: Identificador único del material.

        Returns:
            SpringMaterial si existe, None en caso contrario.
        """
        return await self._db.get(SpringMaterial, material_id)

    async def get_all(self) -> list[SpringMaterial]:
        """
        Retorna todos los materiales del catálogo ordenados por ID.

        Returns:
            Lista completa de materiales disponibles.
        """
        result = await self._db.execute(
            select(SpringMaterial).order_by(SpringMaterial.id)
        )
        return list(result.scalars().all())
