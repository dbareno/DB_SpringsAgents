"""
tests/test_design_history.py
─────────────────────────────────────────────────────────────────────────────
Test suite for design history search and outcome tagging (ADR-7).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DesignProject
from app.schemas.state import UserRequirements, SpringType
from app.services.design_history_service import (
    DesignHistoryService,
    generate_requirement_embedding_key,
)


class TestEmbeddingKeyGeneration:
    """Test requirement embedding key generation for similarity bucketing."""

    def test_same_requirements_produce_same_key(self) -> None:
        """Identical requirements should hash to the same key."""
        req1 = UserRequirements(
            raw_input="Spring 500N 10mm OD25",
            spring_type=SpringType.COMPRESSION,
            load_force_n=500.0,
            deflection_mm=10.0,
            max_outer_diameter_mm=25.0,
            max_free_length_mm=100.0,
        )
        req2 = UserRequirements(
            raw_input="Identical spring specs",
            spring_type=SpringType.COMPRESSION,
            load_force_n=500.0,
            deflection_mm=10.0,
            max_outer_diameter_mm=25.0,
            max_free_length_mm=100.0,
        )

        key1 = generate_requirement_embedding_key(req1)
        key2 = generate_requirement_embedding_key(req2)

        assert key1 == key2, "Identical requirements should produce the same key"

    def test_similar_requirements_produce_same_key(self) -> None:
        """Slightly different requirements within buckets should hash to the same key."""
        req1 = UserRequirements(
            raw_input="Spring 500N 10mm",
            spring_type=SpringType.COMPRESSION,
            load_force_n=500.0,
            deflection_mm=10.0,
            max_outer_diameter_mm=25.0,
            max_free_length_mm=100.0,
        )
        req2 = UserRequirements(
            raw_input="Spring 550N 12mm (similar)",
            spring_type=SpringType.COMPRESSION,
            load_force_n=550.0,  # Within 100N bucket (500-599)
            deflection_mm=12.0,   # Within 5mm bucket (10-14)
            max_outer_diameter_mm=25.0,
            max_free_length_mm=100.0,
        )

        key1 = generate_requirement_embedding_key(req1)
        key2 = generate_requirement_embedding_key(req2)

        assert key1 == key2, "Similar requirements (within buckets) should hash to the same key"

    def test_different_spring_types_produce_different_keys(self) -> None:
        """Different spring types should always produce different keys."""
        req_compression = UserRequirements(
            raw_input="Compression spring",
            spring_type=SpringType.COMPRESSION,
            load_force_n=500.0,
            deflection_mm=10.0,
            max_outer_diameter_mm=25.0,
            max_free_length_mm=100.0,
        )
        req_extension = UserRequirements(
            raw_input="Extension spring",
            spring_type=SpringType.EXTENSION,
            load_force_n=500.0,
            deflection_mm=10.0,
            max_outer_diameter_mm=25.0,
            max_free_length_mm=100.0,
        )

        key1 = generate_requirement_embedding_key(req_compression)
        key2 = generate_requirement_embedding_key(req_extension)

        assert key1 != key2, "Different spring types must produce different keys"

    def test_key_is_deterministic_and_short(self) -> None:
        """Embedding key should be a short hex string, deterministic across calls."""
        req = UserRequirements(
            raw_input="Test spring",
            spring_type=SpringType.COMPRESSION,
            load_force_n=200.0,
            deflection_mm=8.0,
            max_outer_diameter_mm=20.0,
            max_free_length_mm=80.0,
        )

        key1 = generate_requirement_embedding_key(req)
        key2 = generate_requirement_embedding_key(req)

        assert key1 == key2, "Key should be deterministic"
        assert len(key1) == 16, "Key should be 16 hex characters (64 bits)"
        assert all(c in "0123456789abcdef" for c in key1), "Key should be valid hex"


@pytest.mark.asyncio
class TestDesignHistoryService:
    """Test the design history service (similarity search, outcome tagging)."""

    async def test_find_similar_designs_empty_db(
        self, seeded_materials_engine
    ) -> None:
        """Finding similar designs in an empty DB should return empty list."""
        async with seeded_materials_engine() as db_session:
            service = DesignHistoryService(db_session)
            req = UserRequirements(
                raw_input="Test spring",
                spring_type=SpringType.COMPRESSION,
                load_force_n=500.0,
                deflection_mm=10.0,
                max_outer_diameter_mm=25.0,
                max_free_length_mm=100.0,
            )

            similar = await service.find_similar_designs(req, limit=5)
            assert similar == [], "Empty DB should return no similar designs"

    async def test_find_similar_designs_by_embedding_key(
        self, seeded_materials_engine
    ) -> None:
        """Find similar designs by matching embedding key."""
        async with seeded_materials_engine() as db_session:
            service = DesignHistoryService(db_session)
            req = UserRequirements(
                raw_input="Compression spring 500N",
                spring_type=SpringType.COMPRESSION,
                load_force_n=500.0,
                deflection_mm=10.0,
                max_outer_diameter_mm=25.0,
                max_free_length_mm=100.0,
            )

            # Create a past design with the same embedding key
            embedding_key = generate_requirement_embedding_key(req)
            past_project = DesignProject(
                session_id="past_session",
                raw_user_input="Past compression spring 500N",
                spring_type="compression",
                status="approved",
                outcome="won",
                requirement_embedding_key=embedding_key,
            )
            db_session.add(past_project)
            await db_session.commit()

            # Search should find it
            similar = await service.find_similar_designs(req, limit=5)
            assert len(similar) == 1, "Should find the past design with matching embedding"
            assert similar[0]["project_id"] == past_project.id
            assert similar[0]["outcome"] == "won"

    async def test_find_similar_designs_respects_limit(
        self, seeded_materials_engine
    ) -> None:
        """Find similar designs should respect the limit parameter."""
        async with seeded_materials_engine() as db_session:
            service = DesignHistoryService(db_session)
            req = UserRequirements(
                raw_input="Compression spring",
                spring_type=SpringType.COMPRESSION,
                load_force_n=500.0,
                deflection_mm=10.0,
                max_outer_diameter_mm=25.0,
                max_free_length_mm=100.0,
            )

            embedding_key = generate_requirement_embedding_key(req)

            # Create 5 past designs
            for i in range(5):
                project = DesignProject(
                    session_id=f"session_{i}",
                    raw_user_input=f"Past design {i}",
                    spring_type="compression",
                    status="approved",
                    outcome="won" if i % 2 == 0 else "lost",
                    requirement_embedding_key=embedding_key,
                )
                db_session.add(project)
            await db_session.commit()

            # Limit to 3
            similar = await service.find_similar_designs(req, limit=3)
            assert len(similar) == 3, "Should respect limit=3"

    async def test_tag_outcome_success(
        self, seeded_materials_engine
    ) -> None:
        """Tag an existing project with an outcome."""
        async with seeded_materials_engine() as db_session:
            service = DesignHistoryService(db_session)

            # Create a project
            project = DesignProject(
                session_id="test_session",
                raw_user_input="Test spring",
                spring_type="compression",
                status="approved",
                outcome=None,  # Start untagged
            )
            db_session.add(project)
            await db_session.commit()
            project_id = project.id

            # Tag it
            success = await service.tag_outcome(project_id, "won")
            assert success is True, "Tagging should succeed"

            # Verify it was updated
            stmt = select(DesignProject).where(DesignProject.id == project_id)
            result = await db_session.execute(stmt)
            updated_project = result.scalar_one()
            assert updated_project.outcome == "won", "Outcome should be updated to 'won'"

    async def test_tag_outcome_invalid_outcome(
        self, seeded_materials_engine
    ) -> None:
        """Tagging with an invalid outcome should fail."""
        async with seeded_materials_engine() as db_session:
            service = DesignHistoryService(db_session)

            project = DesignProject(
                session_id="test",
                raw_user_input="Test",
                spring_type="compression",
                status="approved",
            )
            db_session.add(project)
            await db_session.commit()
            project_id = project.id

            # Try to tag with invalid outcome
            success = await service.tag_outcome(project_id, "invalid")
            assert success is False, "Invalid outcome should fail"

    async def test_tag_outcome_nonexistent_project(
        self, seeded_materials_engine
    ) -> None:
        """Tagging a nonexistent project should fail gracefully."""
        async with seeded_materials_engine() as db_session:
            service = DesignHistoryService(db_session)

            success = await service.tag_outcome(999, "won")
            assert success is False, "Tagging nonexistent project should fail"

    async def test_get_outcome_summary(
        self, seeded_materials_engine
    ) -> None:
        """Get aggregate outcome statistics."""
        async with seeded_materials_engine() as db_session:
            service = DesignHistoryService(db_session)

            # Create projects with different outcomes
            for outcome, count in [("won", 3), ("lost", 2), ("pending", 1)]:
                for i in range(count):
                    project = DesignProject(
                        session_id=f"session_{outcome}_{i}",
                        raw_user_input=f"Design with {outcome}",
                        spring_type="compression",
                        status="approved",
                        outcome=outcome,
                    )
                    db_session.add(project)
            await db_session.commit()

            summary = await service.get_outcome_summary()
            assert summary["won"] == 3
            assert summary["lost"] == 2
            assert summary["pending"] == 1
            assert summary["total"] == 6
