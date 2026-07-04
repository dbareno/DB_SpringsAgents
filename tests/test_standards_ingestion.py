"""
tests/test_standards_ingestion.py
─────────────────────────────────────────────────────────────────────────────
Unit tests for the sqlite-vec backed standards ingestion pipeline.

Rehabilitated for Phase 2 (ADR-3): the store moved from ChromaDB (broken —
``onnxruntime`` DLL locally, missing ``chromadb.telemetry.product.posthog``
when frozen) to ``sqlite-vec``, which has no such runtime dependency. This
file no longer needs to be ignored in the regression gate.

Run with:
    pytest tests/test_standards_ingestion.py -v
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.standards import ingestion, store


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    """Point the standards store at a fresh temp SQLite file per test."""
    monkeypatch.setattr(store, "get_standards_db_path", lambda: tmp_path / "standards.db")
    store.reset_connection_cache()
    yield
    store.reset_connection_cache()


class TestChunkText:
    """Verify the chunking helper produces well-formed, overlapping chunks."""

    def test_chunk_text_respects_size(self):
        text = "word " * 300  # ~1500 chars
        chunks = ingestion.chunk_text(text, chunk_size=500, overlap=100)
        assert len(chunks) > 1
        for chunk in chunks[:-1]:
            assert len(chunk) <= 500

    def test_chunk_text_has_overlap(self):
        text = "abcdefghij" * 100  # 1000 chars, no whitespace boundaries
        chunks = ingestion.chunk_text(text, chunk_size=500, overlap=100)
        assert len(chunks) >= 2
        # The tail of chunk[0] should reappear at the head of chunk[1]
        assert chunks[0][-50:] in chunks[1]

    def test_chunk_text_empty_input(self):
        assert ingestion.chunk_text("") == []
        assert ingestion.chunk_text("   \n\t  ") == []

    def test_chunk_text_short_input_single_chunk(self):
        chunks = ingestion.chunk_text("A short clause.", chunk_size=500, overlap=100)
        assert chunks == ["A short clause."]

    def test_chunk_size_must_exceed_overlap(self):
        with pytest.raises(ValueError):
            ingestion.chunk_text("some text", chunk_size=100, overlap=100)


class TestIngestDocument:
    """Integration tests for ingest_document() against a real text file."""

    def _write_sample(self, tmp_path: Path, name: str = "din_9999_test.txt") -> Path:
        content = (
            "Clause 1: The spring index shall be between 4 and 20. " * 20
        )
        file_path = tmp_path / name
        file_path.write_text(content, encoding="utf-8")
        return file_path

    def test_ingest_new_document_inserts_rows(self, tmp_path):
        file_path = self._write_sample(tmp_path)
        inserted = ingestion.ingest_document("DIN 9999", file_path)
        assert inserted > 0

        conn = store.get_connection()
        count = conn.execute(
            "SELECT COUNT(*) FROM standards_documents WHERE standard_name = ?",
            ("DIN 9999",),
        ).fetchone()[0]
        assert count == inserted

    def test_ingest_idempotent_skips_existing(self, tmp_path):
        file_path = self._write_sample(tmp_path)
        first = ingestion.ingest_document("DIN 9999", file_path)
        second = ingestion.ingest_document("DIN 9999", file_path)
        assert first > 0
        assert second == 0

    def test_ingest_force_reingests(self, tmp_path):
        file_path = self._write_sample(tmp_path)
        first = ingestion.ingest_document("DIN 9999", file_path)
        second = ingestion.ingest_document("DIN 9999", file_path, force=True)
        assert first == second
        assert first > 0

    def test_ingest_empty_file_returns_zero(self, tmp_path):
        file_path = tmp_path / "empty.txt"
        file_path.write_text("   ", encoding="utf-8")
        inserted = ingestion.ingest_document("EMPTY_STD", file_path)
        assert inserted == 0

    def test_standard_already_ingested_flag(self, tmp_path):
        file_path = self._write_sample(tmp_path)
        assert ingestion.standard_already_ingested("DIN 9999") is False
        ingestion.ingest_document("DIN 9999", file_path)
        assert ingestion.standard_already_ingested("DIN 9999") is True

    def test_vector_row_written_for_each_chunk(self, tmp_path):
        file_path = self._write_sample(tmp_path)
        inserted = ingestion.ingest_document("DIN 9999", file_path)

        conn = store.get_connection()
        vec_count = conn.execute(
            "SELECT COUNT(*) FROM standards_vectors"
        ).fetchone()[0]
        assert vec_count == inserted


class TestIngestDirectory:
    """Tests for ingest_directory() scanning a folder of standards files."""

    def test_ingest_directory_processes_all_files(self, tmp_path):
        (tmp_path / "std_a.txt").write_text("Std A clause text. " * 30, encoding="utf-8")
        (tmp_path / "std_b.txt").write_text("Std B clause text. " * 30, encoding="utf-8")
        (tmp_path / "ignore_me.md").write_text("not a standard", encoding="utf-8")

        results = ingestion.ingest_directory(tmp_path)

        assert "std_a" in results
        assert "std_b" in results
        assert "ignore_me" not in results
        assert all(count > 0 for count in results.values())

    def test_ingest_directory_missing_dir_returns_empty(self, tmp_path):
        missing = tmp_path / "does_not_exist"
        results = ingestion.ingest_directory(missing)
        assert results == {}

    def test_ingest_directory_idempotent(self, tmp_path):
        (tmp_path / "std_a.txt").write_text("Std A clause text. " * 30, encoding="utf-8")
        first = ingestion.ingest_directory(tmp_path)
        second = ingestion.ingest_directory(tmp_path)
        assert sum(first.values()) > 0
        assert sum(second.values()) == 0
