"""
app/db/repositories/material_repository.py
─────────────────────────────────────────────────────────────────────────────
Repositorio para la entidad SpringMaterial.

Encapsula todas las consultas a la tabla ``spring_materials``.
"""

from __future__ import annotations

from typing import Any

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

    async def get_by_name(self, name: str) -> SpringMaterial | None:
        """
        Retorna un material por su nombre único.

        Args:
            name: Nombre exacto del material.

        Returns:
            SpringMaterial si existe, None en caso contrario.
        """
        result = await self._db.execute(
            select(SpringMaterial).where(SpringMaterial.name == name)
        )
        return result.scalar_one_or_none()

    async def get_all(self, *, active_only: bool = True) -> list[SpringMaterial]:
        """
        Retorna todos los materiales del catálogo ordenados por ID.

        Args:
            active_only: Si es True (default), excluye materiales
                desactivados (soft-deleted).

        Returns:
            Lista completa de materiales disponibles.
        """
        stmt = select(SpringMaterial).order_by(SpringMaterial.id)
        if active_only:
            stmt = stmt.where(SpringMaterial.active.is_(True))
        result = await self._db.execute(stmt)
        return list(result.scalars().all())

    async def list_filtered(
        self,
        *,
        min_operating_temperature_c: float | None = None,
        corrosion_resistant: bool | None = None,
        cyclic_load: bool = False,
        max_cost_usd_per_kg: float | None = None,
        min_yield_strength_mpa: float | None = None,
        active_only: bool = True,
    ) -> list[SpringMaterial]:
        """
        Retorna materiales que cumplen los filtros de ingeniería indicados.

        Args:
            min_operating_temperature_c: Temperatura mínima que el material
                debe soportar (filtra ``max_temp_c >= valor``).
            corrosion_resistant: Si True, exige resistencia a la corrosión.
                None u False no filtran por esta propiedad.
            cyclic_load: Reservado para futura ponderación de fatiga; no
                filtra resultados hoy (el scoring vive en app/tools/materials.py).
            max_cost_usd_per_kg: Techo de costo opcional (USD/kg).
            min_yield_strength_mpa: Límite elástico mínimo requerido (MPa).
            active_only: Si es True (default), excluye materiales desactivados.

        Returns:
            Lista de materiales que satisfacen todos los filtros, ordenada
            por ID.
        """
        stmt = select(SpringMaterial).order_by(SpringMaterial.id)

        if active_only:
            stmt = stmt.where(SpringMaterial.active.is_(True))
        if min_operating_temperature_c is not None:
            stmt = stmt.where(
                SpringMaterial.max_temp_c >= min_operating_temperature_c
            )
        if corrosion_resistant:
            stmt = stmt.where(SpringMaterial.corrosion_resistant.is_(True))
        if max_cost_usd_per_kg is not None:
            stmt = stmt.where(SpringMaterial.cost_usd_per_kg <= max_cost_usd_per_kg)
        if min_yield_strength_mpa is not None:
            stmt = stmt.where(
                SpringMaterial.yield_strength_mpa >= min_yield_strength_mpa
            )

        result = await self._db.execute(stmt)
        return list(result.scalars().all())

    async def create(self, data: dict[str, Any]) -> SpringMaterial:
        """
        Crea un nuevo material y lo persiste en base de datos.

        Args:
            data: Campos del material (deben coincidir con columnas del
                modelo ``SpringMaterial``).

        Returns:
            La instancia de SpringMaterial creada (con ID asignado tras flush).
        """
        material = SpringMaterial(**data)
        self._db.add(material)
        await self._db.flush()
        return material

    async def update(
        self, material_id: int, data: dict[str, Any]
    ) -> SpringMaterial | None:
        """
        Actualiza un material existente con los campos provistos.

        Args:
            material_id: ID del material a actualizar.
            data: Campos a modificar (solo los presentes se actualizan).

        Returns:
            SpringMaterial actualizado, o None si no se encontró el ID.
        """
        material = await self.get_by_id(material_id)
        if material is None:
            return None
        for key, value in data.items():
            if value is not None and hasattr(material, key):
                setattr(material, key, value)
        await self._db.flush()
        return material

    async def deactivate(self, material_id: int) -> SpringMaterial | None:
        """
        Soft-delete: marca ``active=False`` sin eliminar la fila.

        Preserva la integridad referencial con
        ``design_iterations.material_id`` para diseños históricos.

        Args:
            material_id: ID del material a desactivar.

        Returns:
            SpringMaterial actualizado, o None si no se encontró el ID.
        """
        material = await self.get_by_id(material_id)
        if material is None:
            return None
        material.active = False
        await self._db.flush()
        return material
