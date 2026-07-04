"""
tests/test_standards_retrieval.py
─────────────────────────────────────────────────────────────────────────────
Integration tests for the offline embedder (``app/standards/embedder.py``)
and retrieval (``app/standards/retrieval.py``) modules that replace the
broken ChromaDB-based standards RAG.
"""

from __future__ import annotations

import pytest

from app.standards import ingestion, retrieval, store
from app.standards.embedder import EMBEDDING_DIM, embed_text


class TestEmbedder:
    """The embedder must be deterministic and offline (no network/model)."""

    def test_same_query_gives_same_vector(self):
        v1 = embed_text("shear stress limits for compression springs")
        v2 = embed_text("shear stress limits for compression springs")
        assert v1 == v2

    def test_vector_has_expected_dimension(self):
        vec = embed_text("spring index range requirements")
        assert len(vec) == EMBEDDING_DIM

    def test_different_text_gives_different_vector(self):
        v1 = embed_text("compression spring buckling")
        v2 = embed_text("torsion spring bending stress")
        assert v1 != v2

    def test_empty_text_returns_zero_vector(self):
        vec = embed_text("")
        assert len(vec) == EMBEDDING_DIM
        assert all(x == 0.0 for x in vec)

    def test_vector_is_l2_normalized(self):
        vec = embed_text("fatigue safety factor Goodman criterion")
        norm = sum(x * x for x in vec) ** 0.5
        assert norm == pytest.approx(1.0, abs=1e-5)

    def test_similar_text_more_similar_than_unrelated(self):
        """Cosine similarity sanity check: related phrasing should score
        closer than an unrelated query."""
        base = embed_text("shear stress limits and safety factors")
        similar = embed_text("shear stress limit and safety factor")
        unrelated = embed_text("quotation lot size pricing margin")

        def cosine(a, b):
            dot = sum(x * y for x, y in zip(a, b))
            return dot  # already L2-normalized

        assert cosine(base, similar) > cosine(base, unrelated)


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    """Point the standards store at a fresh temp SQLite file per test."""
    monkeypatch.setattr(store, "get_standards_db_path", lambda: tmp_path / "standards.db")
    store.reset_connection_cache()
    yield
    store.reset_connection_cache()


class TestRetrieveStandards:
    """Tests for retrieve_standards() against a seeded temp store."""

    def _seed(self, tmp_path):
        din_file = tmp_path / "din_2095.txt"
        din_file.write_text(
            "Clause 4.1: The spring index C shall be between 4 and 20 for "
            "compression springs. Clause 5.3: The slenderness ratio L0/D "
            "shall not exceed 5.26 to avoid lateral buckling. "
            * 5,
            encoding="utf-8",
        )
        ingestion.ingest_document("DIN 2095", din_file)

    def test_retrieve_returns_relevant_chunks(self, tmp_path):
        self._seed(tmp_path)
        results = retrieval.retrieve_standards("spring index range compression", top_k=3)
        assert len(results) > 0
        assert all(r.standard_name == "DIN 2095" for r in results)

    def test_retrieve_respects_top_k(self, tmp_path):
        self._seed(tmp_path)
        results = retrieval.retrieve_standards("buckling slenderness ratio", top_k=1)
        assert len(results) <= 1

    def test_retrieve_empty_store_returns_empty(self, tmp_path):
        results = retrieval.retrieve_standards("anything at all")
        assert results == []

    def test_retrieve_blank_query_returns_empty(self, tmp_path):
        self._seed(tmp_path)
        assert retrieval.retrieve_standards("") == []
        assert retrieval.retrieve_standards("   ") == []

    def test_retrieve_results_ordered_by_distance(self, tmp_path):
        self._seed(tmp_path)
        results = retrieval.retrieve_standards("spring index compression", top_k=5)
        distances = [r.distance for r in results]
        assert distances == sorted(distances)

    def test_retrieve_never_raises_on_backend_failure(self, tmp_path, monkeypatch):
        self._seed(tmp_path)

        def _boom():
            raise RuntimeError("simulated backend failure")

        monkeypatch.setattr(retrieval, "get_connection", _boom)
        results = retrieval.retrieve_standards("spring index")
        assert results == []
