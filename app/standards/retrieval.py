"""
app/standards/retrieval.py
─────────────────────────────────────────────────────────────────────────────
Retrieval API for the sqlite-vec backed standards store.

Used by ``app/agents/agent4_compliance.py`` to cite relevant DIN/ASTM clauses
in the compliance report. This is advisory/explanatory only — the hardcoded
compliance checks in ``app/tools/compliance.py`` remain the primary,
unchanged pass/fail logic.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.standards.embedder import embed_text
from app.standards.store import get_connection, serialize_embedding

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StandardsChunk:
    """A single retrieved standards passage."""

    standard_name: str
    chunk_index: int
    chunk_text: str
    distance: float


def retrieve_standards(query: str, top_k: int = 3) -> list[StandardsChunk]:
    """
    Retrieve the ``top_k`` most relevant standards chunks for ``query`` using
    cosine-style distance search over ``sqlite-vec``.

    Args:
        query: Natural-language description of the design condition to check.
        top_k: Maximum number of chunks to return.

    Returns:
        List of :class:`StandardsChunk`, ordered by ascending distance
        (closest match first). Empty list if the store is empty, the query
        is blank, or retrieval fails for any reason (never raises — callers
        must be able to fall back gracefully).
    """
    if not query or not query.strip():
        return []

    try:
        conn = get_connection()

        row_count = conn.execute("SELECT COUNT(*) FROM standards_documents").fetchone()[0]
        if row_count == 0:
            logger.info("Standards store is empty. No chunks to retrieve.")
            return []

        query_embedding = embed_text(query)
        query_blob = serialize_embedding(query_embedding)

        rows = conn.execute(
            """
            SELECT d.standard_name, d.chunk_index, d.chunk_text, v.distance
            FROM standards_vectors v
            JOIN standards_documents d ON d.id = v.rowid
            WHERE v.embedding MATCH ? AND k = ?
            ORDER BY v.distance
            """,
            (query_blob, min(top_k, row_count)),
        ).fetchall()

        return [
            StandardsChunk(
                standard_name=row[0],
                chunk_index=row[1],
                chunk_text=row[2],
                distance=float(row[3]),
            )
            for row in rows
        ]
    except Exception as exc:
        logger.warning(
            "Standards retrieval failed (falling back to no citations): %s", exc
        )
        return []
