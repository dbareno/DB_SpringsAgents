"""
tests/test_standards_ingestion.py
─────────────────────────────────────────────────────────────────────────────
Unit tests for PDF standards ingestion into ChromaDB.

Run with:
    pytest tests/test_standards_ingestion.py -v
"""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Any

import pytest

from app.db.chromadb_client import (
    get_standards_collection,
    get_standards_collection_stats,
    ingest_standards,
    query_standards,
)

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

DOCS_DIR = Path(__file__).resolve().parent.parent / "docs"


def _make_sample_chunks() -> list[dict[str, Any]]:
    """Produce representative ChromaDB chunk dicts for testing."""
    chunks = [
        {
            "id": hashlib.md5(b"BS-EN-13906-1-2013|4.1|0").hexdigest()[:16],
            "document": (
                "BS EN 13906-1 §4.1: The spring index C = D/d shall be between "
                "4 and 20 for helical compression springs. Values outside this "
                "range increase manufacturing difficulty and may cause buckling."
            ),
            "metadata": {
                "standard": "BS-EN-13906-1-2013",
                "section": "4.1",
                "page": 12,
                "spring_type": "compression",
            },
        },
        {
            "id": hashlib.md5(b"BS-EN-13906-1-2013|5.3|1").hexdigest()[:16],
            "document": (
                "BS EN 13906-1 §5.3: The slenderness ratio L0/D shall not exceed "
                "5.26 for springs with fixed-free end conditions to avoid lateral "
                "buckling."
            ),
            "metadata": {
                "standard": "BS-EN-13906-1-2013",
                "section": "5.3",
                "page": 18,
                "spring_type": "compression",
            },
        },
        {
            "id": hashlib.md5(b"DIN EN 10270-1|7.2|0").hexdigest()[:16],
            "document": (
                "DIN EN 10270-1 §7.2: Patented cold-drawn unalloyed steel wire "
                "for mechanical springs. Tensile strength grades A, B, C, D, F "
                "and G cover a range from 1200 to 2200 MPa."
            ),
            "metadata": {
                "standard": "DIN EN 10270-1",
                "section": "7.2",
                "page": 8,
                "spring_type": "all",
            },
        },
        {
            "id": hashlib.md5(b"BS-EN-ISO-26909-2010-en|6|0").hexdigest()[:16],
            "document": (
                "ISO 26909 §6: Design verification for helical compression springs "
                "shall include shear stress calculation using the Wahl correction "
                "factor, buckling analysis, and spring index validation."
            ),
            "metadata": {
                "standard": "BS-EN-ISO-26909-2010-en",
                "section": "6",
                "page": 22,
                "spring_type": "all",
            },
        },
    ]
    return chunks


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestChunkStructure:
    """Verify that ingestion chunks have the expected structure."""

    def test_chunk_has_all_required_keys(self):
        chunks = _make_sample_chunks()
        for chunk in chunks:
            assert "id" in chunk, "Chunk missing 'id'"
            assert "document" in chunk, "Chunk missing 'document'"
            assert "metadata" in chunk, "Chunk missing 'metadata'"
            meta = chunk["metadata"]
            assert "standard" in meta, "Metadata missing 'standard'"
            assert "section" in meta, "Metadata missing 'section'"
            assert "page" in meta, "Metadata missing 'page'"
            assert "spring_type" in meta, "Metadata missing 'spring_type'"

    def test_chunk_document_not_empty(self):
        chunks = _make_sample_chunks()
        for chunk in chunks:
            assert len(chunk["document"]) >= 50, (
                f"Chunk {chunk['id']} is too short: {len(chunk['document'])} chars"
            )

    def test_chunk_id_is_consistent(self):
        """Same content should always produce the same ID."""
        chunks_a = _make_sample_chunks()
        chunks_b = _make_sample_chunks()
        for a, b in zip(chunks_a, chunks_b):
            assert a["id"] == b["id"], "ID scheme is not deterministic"


class TestIngestionFlow:
    """Integration tests for the ingest_standards() function."""

    @pytest.fixture(autouse=True)
    def _clean_collection(self) -> None:
        """Ensure a clean collection before each test."""
        collection = get_standards_collection()
        existing = collection.get()
        if existing["ids"]:
            collection.delete(ids=existing["ids"])

    def test_ingest_new_documents(self):
        chunks = _make_sample_chunks()
        count = ingest_standards(documents=chunks)
        assert count == len(chunks), (
            f"Expected {len(chunks)} ingested, got {count}"
        )

    def test_ingest_idempotent(self):
        """Re-ingesting the same chunks should return 0."""
        chunks = _make_sample_chunks()
        ingest_standards(documents=chunks)
        count = ingest_standards(documents=chunks)
        assert count == 0, "Idempotent re-ingestion should return 0"

    def test_ingest_force_replaces_all(self):
        chunks = _make_sample_chunks()
        ingest_standards(documents=chunks)

        # Force re-ingest with different data
        new_chunks = [
            {
                "id": "force-test-1",
                "document": "Force re-ingest test clause.",
                "metadata": {
                    "standard": "TEST",
                    "section": "1",
                    "page": 1,
                    "spring_type": "all",
                },
            }
        ]
        count = ingest_standards(documents=new_chunks, force=True)
        assert count == 1, (
            f"After force, expected 1 ingested, got {count}"
        )

        # Only the new chunk should remain
        stats = get_standards_collection_stats()
        assert stats["total_documents"] == 1

    def test_ingest_mixed_new_and_existing(self):
        chunks = _make_sample_chunks()
        ingest_standards(documents=chunks)

        # Add one new, keep one existing
        extra = {
            "id": "extra-test-1",
            "document": "Additional test clause.",
            "metadata": {
                "standard": "TEST",
                "section": "annex",
                "page": 99,
                "spring_type": "all",
            },
        }
        mixed = chunks[:2] + [extra]
        count = ingest_standards(documents=mixed)
        assert count == 1, "Only the new chunk should be ingested"

    def test_empty_chunks_list(self):
        count = ingest_standards(documents=[])
        assert count == 0, "Empty list should ingest 0 documents"


class TestQueryStandards:
    """Tests for query_standards() with ingested data."""

    @pytest.fixture(autouse=True)
    def _seed_data(self) -> None:
        """Ensure the collection has known test data."""
        collection = get_standards_collection()
        existing = collection.get()
        if existing["ids"]:
            collection.delete(ids=existing["ids"])
        chunks = _make_sample_chunks()
        ingest_standards(documents=chunks, force=True)

    def test_query_compression_returns_results(self):
        results = query_standards(
            query_text="shear stress limits for compression springs",
            spring_type="compression",
            n_results=3,
        )
        assert len(results) > 0, "Should return results for compression"
        for r in results:
            assert "document" in r
            assert "metadata" in r
            assert "distance" in r
            assert "chunk_id" in r

    def test_query_with_list_spring_type(self):
        results = query_standards(
            query_text="spring index range",
            spring_type=["compression", "extension"],
            n_results=5,
        )
        assert len(results) > 0

    def test_query_returns_results_for_all_type(self):
        """Clauses tagged 'all' should be included regardless of filter."""
        results = query_standards(
            query_text="spring design verification",
            spring_type="compression",
            n_results=5,
        )
        standards_found = {
            r["metadata"].get("standard") for r in results if r.get("metadata")
        }
        # 'all' type documents should appear in compression queries
        assert any("DIN EN 10270" in s for s in standards_found if s), (
            "Clauses with spring_type='all' should be returned"
        )

    def test_query_empty_collection_returns_empty(self):
        """Query on an empty collection should return empty list."""
        collection = get_standards_collection()
        collection.delete(ids=collection.get()["ids"])

        results = query_standards(
            query_text="anything",
            spring_type="compression",
        )
        assert results == []


class TestCollectionStats:
    """Tests for get_standards_collection_stats()."""

    @pytest.fixture(autouse=True)
    def _clean_and_seed(self) -> None:
        collection = get_standards_collection()
        existing = collection.get()
        if existing["ids"]:
            collection.delete(ids=existing["ids"])
        chunks = _make_sample_chunks()
        ingest_standards(documents=chunks, force=True)

    def test_stats_returns_expected_keys(self):
        stats = get_standards_collection_stats()
        assert "total_documents" in stats
        assert "by_standard" in stats
        assert "by_spring_type" in stats

    def test_stats_total_count(self):
        stats = get_standards_collection_stats()
        assert stats["total_documents"] == 4

    def test_stats_by_standard(self):
        stats = get_standards_collection_stats()
        assert "BS-EN-13906-1-2013" in stats["by_standard"]
        assert "DIN EN 10270-1" in stats["by_standard"]

    def test_stats_by_spring_type(self):
        stats = get_standards_collection_stats()
        assert stats["by_spring_type"].get("compression", 0) >= 2
        assert stats["by_spring_type"].get("all", 0) >= 2


class TestAgent4Integration:
    """Verify the agent4 compliance node integrates with ChromaDB correctly."""

    @pytest.fixture(autouse=True)
    def _seed_standards(self) -> None:
        """Seed the collection with test data for agent queries."""
        collection = get_standards_collection()
        existing = collection.get()
        if existing["ids"]:
            collection.delete(ids=existing["ids"])
        chunks = _make_sample_chunks()
        ingest_standards(documents=chunks, force=True)

    def test_retrieved_clauses_added_to_compliance_report(self):
        """The compliance report should include retrieved_standards when queried."""
        from app.schemas.state import ComplianceReport

        spring_type = "compression"

        from app.db.chromadb_client import query_standards

        results = query_standards(
            query_text="compression spring design shear stress limits",
            spring_type=spring_type,
            n_results=3,
        )
        report = ComplianceReport(
            approved=True,
            safety_factor_shear=2.1,
            safety_factor_buckling=1.8,
            applicable_standard="BS EN 13906-1",
            failure_modes=[],
            redesign_directives=[],
            retrieved_standards=[r["document"] for r in results],
            standards_referenced=list({
                r["metadata"].get("standard", "unknown")
                for r in results
                if r.get("metadata")
            }),
        )
        assert len(report.retrieved_standards) > 0
        assert len(report.standards_referenced) > 0
        assert "BS-EN-13906-1-2013" in report.standards_referenced


class TestPdfExtractionHelpers:
    """Test the chunking helpers from the ingestion script."""

    def test_normalise_standard_name(self):
        from scripts.ingest_standards import _normalise_standard_name

        assert _normalise_standard_name(
            "393310294-BS-EN-13906-1-2013.pdf"
        ) == "BS-EN-13906-1-2013"
        assert _normalise_standard_name(
            "DIN EN 10270 - 1 Steel wire for mechanical springs.pdf"
        ) == "DIN EN 10270 - 1 Steel wire for mechanical springs"

    def test_infer_spring_type_from_filename(self):
        from scripts.ingest_standards import _infer_spring_type

        assert _infer_spring_type(
            "393310294-BS-EN-13906-1-2013.pdf", ""
        ) == "compression"
        assert _infer_spring_type(
            "BS-EN-10089-for-Hot-Rolled-Steel-of-Quenched-and-Tem.pdf", ""
        ) == "compression"
        assert _infer_spring_type(
            "DIN EN 10270 - 1 Steel wire for mechanical springs.pdf", ""
        ) == "all"

    def test_infer_spring_type_from_text(self):
        from scripts.ingest_standards import _infer_spring_type

        text = "Helical compression springs shall be designed..."
        assert _infer_spring_type("unknown.pdf", text) == "compression"

        text = "Extension springs initial tension requirements..."
        assert _infer_spring_type("unknown.pdf", text) == "extension"

        text = "Torsion springs bending stress limits..."
        assert _infer_spring_type("unknown.pdf", text) == "torsion"

    def test_is_section_boundary(self):
        from scripts.ingest_standards import _is_section_boundary

        assert _is_section_boundary("§ 5.3 Slenderness ratio") is True
        assert _is_section_boundary("Clause 4 Spring index") is True
        assert _is_section_boundary("5.1  General requirements") is True
        assert _is_section_boundary("Annex A Normative") is True
        assert _is_section_boundary("This is just a normal paragraph.") is False
        assert _is_section_boundary("42") is False  # page number alone
