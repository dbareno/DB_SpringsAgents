"""
app/standards/ingestion.py
─────────────────────────────────────────────────────────────────────────────
Ingestion pipeline for DIN/ASTM standard documents into the sqlite-vec
backed standards store.

Reads PDFs (or plain ``.txt`` files, used for lightweight test/sample
standards) from a local directory, extracts text, chunks it, embeds each
chunk, and stores it in ``standards_documents`` / ``standards_vectors``.

Idempotent: re-running ingestion for a ``standard_name`` that already has
rows is a no-op (skipped), unless ``force=True``.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from pypdf import PdfReader

from app.standards.embedder import embed_text
from app.standards.store import get_connection, serialize_embedding

logger = logging.getLogger(__name__)


def _default_standards_dir() -> Path:
    """
    Resolve the bundled starter-standards directory.

    - Frozen (.exe) mode: ``launcher.spec`` bundles ``data/standards`` at the
      PyInstaller bundle root (``sys._MEIPASS/data/standards``).
    - Dev/server mode: project-local ``./data/standards``.
    """
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "data" / "standards"  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent.parent.parent / "data" / "standards"


DEFAULT_STANDARDS_DIR = _default_standards_dir()

CHUNK_SIZE = 500
CHUNK_OVERLAP = 100


def _extract_text(file_path: Path) -> str:
    """Extract raw text from a PDF or plain-text standards document."""
    if file_path.suffix.lower() == ".pdf":
        reader = PdfReader(str(file_path))
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n".join(pages)
    return file_path.read_text(encoding="utf-8", errors="ignore")


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """
    Split ``text`` into overlapping fixed-size character chunks.

    Args:
        text: Full document text.
        chunk_size: Target chunk length in characters.
        overlap: Number of characters shared between consecutive chunks.

    Returns:
        List of non-empty, whitespace-stripped chunk strings.
    """
    normalized = " ".join(text.split())
    if not normalized:
        return []

    if chunk_size <= overlap:
        raise ValueError("chunk_size must be greater than overlap")

    chunks: list[str] = []
    step = chunk_size - overlap
    start = 0
    n = len(normalized)
    while start < n:
        end = min(start + chunk_size, n)
        chunk = normalized[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end == n:
            break
        start += step

    return chunks


def standard_already_ingested(standard_name: str) -> bool:
    """Return True if ``standard_name`` already has rows in the store."""
    conn = get_connection()
    row = conn.execute(
        "SELECT 1 FROM standards_documents WHERE standard_name = ? LIMIT 1",
        (standard_name,),
    ).fetchone()
    return row is not None


def ingest_document(
    standard_name: str,
    file_path: Path,
    force: bool = False,
) -> int:
    """
    Ingest a single standards document (PDF or text) into the store.

    Args:
        standard_name: Logical name of the standard (e.g. "DIN 2095"), used
            as the idempotency key.
        file_path: Path to the source PDF/text file.
        force: If True, delete existing chunks for this standard and
            re-ingest from scratch.

    Returns:
        Number of chunks inserted (0 if skipped because already ingested).
    """
    conn = get_connection()

    if force:
        conn.execute(
            "DELETE FROM standards_vectors WHERE rowid IN ("
            "SELECT id FROM standards_documents WHERE standard_name = ?)",
            (standard_name,),
        )
        conn.execute(
            "DELETE FROM standards_documents WHERE standard_name = ?",
            (standard_name,),
        )
        conn.commit()
    elif standard_already_ingested(standard_name):
        logger.info(
            "Standard %r already ingested. Skipping (idempotent).", standard_name
        )
        return 0

    text = _extract_text(file_path)
    chunks = chunk_text(text)
    if not chunks:
        logger.warning("No text extracted from %s — nothing to ingest.", file_path)
        return 0

    inserted = 0
    for chunk_index, chunk in enumerate(chunks):
        cursor = conn.execute(
            "INSERT INTO standards_documents "
            "(standard_name, chunk_index, chunk_text, source_file) "
            "VALUES (?, ?, ?, ?)",
            (standard_name, chunk_index, chunk, str(file_path)),
        )
        doc_id = cursor.lastrowid
        embedding = embed_text(chunk)
        conn.execute(
            "INSERT INTO standards_vectors (rowid, embedding) VALUES (?, ?)",
            (doc_id, serialize_embedding(embedding)),
        )
        inserted += 1

    conn.commit()
    logger.info(
        "Ingested %d chunks for standard %r from %s.",
        inserted, standard_name, file_path.name,
    )
    return inserted


def ingest_directory(
    directory: Path | None = None,
    force: bool = False,
) -> dict[str, int]:
    """
    Ingest every PDF/text standards document found in ``directory``.

    The standard name is derived from the file stem (e.g.
    ``din_2095_excerpt.pdf`` → ``"din_2095_excerpt"``).

    Args:
        directory: Directory to scan. Defaults to ``data/standards/``.
        force: Passed through to :func:`ingest_document`.

    Returns:
        Dict mapping standard name to number of chunks inserted.
    """
    scan_dir = directory or DEFAULT_STANDARDS_DIR
    if not scan_dir.is_dir():
        logger.warning("Standards directory %s does not exist. Nothing to ingest.", scan_dir)
        return {}

    results: dict[str, int] = {}
    for file_path in sorted(scan_dir.iterdir()):
        if file_path.suffix.lower() not in (".pdf", ".txt"):
            continue
        standard_name = file_path.stem
        results[standard_name] = ingest_document(standard_name, file_path, force=force)

    return results
