"""
app/services/design_history_service.py
─────────────────────────────────────────────────────────────────────────────
Design history search and outcome tagging service (ADR-7).

Provides:
- Similarity search over past designs by requirement features
- Outcome tagging (won/lost/pending) for learning
- Requirement embedding key generation for SQL-based similarity
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DesignProject
from app.schemas.state import UserRequirements

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Requirement embedding / hashing (for SQL-based similarity without ML)
# ─────────────────────────────────────────────────────────────────────────────


def _normalize_requirement_feature_string(req: UserRequirements) -> str:
    """
    Normalize a UserRequirements object into a canonical string for similarity.

    This is used as a requirement embedding key (ADR-7) for lightweight
    SQL-based similarity without external ML. Hash buckets group similar designs
    (same spring_type + approximate load/deflection/envelope).

    Strategy: normalize numeric fields to buckets so minor variations (e.g.,
    499N vs 500N load) map to the same hash.
    """
    # Bucket numeric fields to reduce false negatives from small variations
    def bucket_float(val: float | None, bucket_size: float) -> str:
        if val is None:
            return "none"
        # Use floor division to bucket (e.g., 499N in 100N buckets = 400)
        # This way 499 and 500 both map to 400 if bucket_size=100
        bucketed = (int(val) // int(bucket_size)) * bucket_size
        return f"{bucketed:.0f}"

    feature_dict = {
        "spring_type": req.spring_type or "unknown",
        "load_bucket": bucket_float(req.load_force_n, 100),  # 100N buckets
        "deflection_bucket": bucket_float(req.deflection_mm, 5),  # 5mm buckets
        "rate_bucket": bucket_float(req.spring_rate_n_mm, 10),  # 10 N/mm buckets
        "od_bucket": bucket_float(req.max_outer_diameter_mm, 10),  # 10mm buckets
        "length_bucket": bucket_float(req.max_free_length_mm, 50),  # 50mm buckets
        "temp_bucket": bucket_float(req.operating_temperature_c, 50),  # 50°C buckets
        "corrosion": "yes" if req.corrosion_resistant else "no",
        "cyclic": "yes" if req.cyclic_load else "no",
    }

    # Canonical JSON string for hashing
    canonical = json.dumps(feature_dict, sort_keys=True, separators=(",", ":"))
    return canonical


def generate_requirement_embedding_key(req: UserRequirements) -> str:
    """
    Generate a deterministic embedding key for a requirement set (ADR-7).

    This is stored on design_projects.requirement_embedding_key and is used
    for fast similarity search via SQL (no ML or vector DB required).

    Returns a short hex string suitable for indexing.
    """
    canonical = _normalize_requirement_feature_string(req)
    hash_obj = hashlib.sha256(canonical.encode("utf-8"))
    # Return first 16 hex chars (64 bits of entropy, sufficient for bucketing)
    return hash_obj.hexdigest()[:16]


# ─────────────────────────────────────────────────────────────────────────────
# Design history service
# ─────────────────────────────────────────────────────────────────────────────


class DesignHistoryService:
    """
    Service for querying and tagging design history (ADR-7).

    Usage:
        service = DesignHistoryService(db_session)
        similar = await service.find_similar_designs(requirements, limit=5)
        await service.tag_outcome(project_id, "won")
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_similar_designs(
        self,
        requirements: UserRequirements,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """
        Find past designs similar to the given requirements (ADR-7).

        Similarity is based on bucketed requirement features (spring_type, load,
        deflection, envelope, environment) without ML — just SQL equality on
        the embedding key.

        Args:
            requirements: The current design's requirements
            limit: Maximum number of similar designs to return

        Returns:
            List of dicts with {project_id, spring_type, raw_input, status, outcome, created_at}
        """
        embedding_key = generate_requirement_embedding_key(requirements)
        logger.info("[DesignHistory] Searching for designs with embedding_key=%s", embedding_key)

        # Query: find approved or completed designs with the same embedding key
        stmt = (
            select(DesignProject)
            .where(
                and_(
                    DesignProject.requirement_embedding_key == embedding_key,
                    DesignProject.status.in_(("approved", "completed")),
                    DesignProject.outcome.isnot(None),  # Only designs with tagged outcomes
                )
            )
            .order_by(DesignProject.created_at.desc())
            .limit(limit)
        )

        result = await self._session.execute(stmt)
        rows = result.scalars().all()

        similar_designs = []
        for row in rows:
            similar_designs.append({
                "project_id": row.id,
                "session_id": row.session_id,
                "spring_type": row.spring_type,
                "raw_user_input": row.raw_user_input[:200],  # Truncate for display
                "status": row.status,
                "outcome": row.outcome,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            })

        logger.info(
            "[DesignHistory] Found %d similar designs (embedding=%s)",
            len(similar_designs),
            embedding_key,
        )
        return similar_designs

    async def tag_outcome(
        self,
        project_id: int,
        outcome: str,
    ) -> bool:
        """
        Tag a completed design with an outcome (won/lost/pending) for learning (ADR-7).

        Args:
            project_id: The design_projects.id
            outcome: "won" | "lost" | "pending"

        Returns:
            True if successful, False if project not found
        """
        if outcome not in ("won", "lost", "pending"):
            logger.warning("[DesignHistory] Invalid outcome: %s", outcome)
            return False

        stmt = select(DesignProject).where(DesignProject.id == project_id)
        result = await self._session.execute(stmt)
        project = result.scalar_one_or_none()

        if project is None:
            logger.warning("[DesignHistory] Project %d not found", project_id)
            return False

        project.outcome = outcome
        await self._session.commit()
        logger.info("[DesignHistory] Tagged project %d with outcome=%s", project_id, outcome)
        return True

    async def get_outcome_summary(self) -> dict[str, Any]:
        """
        Get aggregate outcome statistics (for dashboards, optional).

        Returns:
            {"won": N, "lost": N, "pending": N, "total": N}
        """
        stmt = select(DesignProject).where(DesignProject.outcome.isnot(None))
        result = await self._session.execute(stmt)
        all_projects = result.scalars().all()

        outcomes = {}
        for proj in all_projects:
            outcomes[proj.outcome] = outcomes.get(proj.outcome, 0) + 1

        return {
            "won": outcomes.get("won", 0),
            "lost": outcomes.get("lost", 0),
            "pending": outcomes.get("pending", 0),
            "total": len(all_projects),
        }
