"""
app/db/repositories/__init__.py
─────────────────────────────────────────────────────────────────────────────
Repositorios para el patrón Repository — encapsulan el acceso a base de datos.
"""

from app.db.repositories.design_repository import (
    DesignIterationRepository,
    DesignProjectRepository,
)
from app.db.repositories.material_repository import MaterialRepository

__all__ = [
    "DesignIterationRepository",
    "DesignProjectRepository",
    "MaterialRepository",
]
