"""
app/standards/store.py
─────────────────────────────────────────────────────────────────────────────
SQLite + sqlite-vec backed store for standards RAG.

Replaces ChromaDB (``app/db/chromadb_client.py``), which fails both in dev
(``onnxruntime`` DLL load error) and when frozen into the ``.exe``
(``No module named 'chromadb.telemetry.product.posthog'``).

``sqlite-vec`` ships as a plain SQLite loadable extension with a pure-Python
loader (no compiled toolchain, no native dependency tree beyond the SQLite
runtime already bundled with Python) — safe to freeze with PyInstaller.

The store lives in its own SQLite file under the Phase 0 writable data
directory (``app/core/paths.get_data_dir()``), independent from the
SQLAlchemy-managed ``spring_design_agent.db`` used for materials/design
history, because sqlite-vec needs a raw ``sqlite3`` connection with
extension loading enabled — mixing that with the async SQLAlchemy engine
would be needlessly fragile.
"""

from __future__ import annotations

import logging
import sqlite3
import struct
from functools import lru_cache
from pathlib import Path

import sqlite_vec

from app.core.paths import get_data_dir
from app.standards.embedder import EMBEDDING_DIM

logger = logging.getLogger(__name__)

_DB_FILENAME = "standards.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS standards_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    standard_name TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    chunk_text TEXT NOT NULL,
    source_file TEXT,
    UNIQUE(standard_name, chunk_index)
);
"""

_VEC_SCHEMA_TEMPLATE = """
CREATE VIRTUAL TABLE IF NOT EXISTS standards_vectors USING vec0(
    embedding FLOAT[{dim}]
);
"""


def get_standards_db_path() -> Path:
    """Return the on-disk path of the standards SQLite store."""
    return get_data_dir() / _DB_FILENAME


def serialize_embedding(embedding: list[float]) -> bytes:
    """Pack a list of floats into the raw bytes format sqlite-vec expects."""
    return struct.pack(f"{len(embedding)}f", *embedding)


def deserialize_embedding(blob: bytes) -> list[float]:
    """Unpack sqlite-vec's raw float bytes back into a list of floats."""
    count = len(blob) // 4
    return list(struct.unpack(f"{count}f", blob))


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.execute(_SCHEMA)
    conn.execute(_VEC_SCHEMA_TEMPLATE.format(dim=EMBEDDING_DIM))
    conn.commit()
    return conn


@lru_cache(maxsize=1)
def get_connection() -> sqlite3.Connection:
    """
    Return a cached, schema-initialized sqlite3 connection to the standards
    store, with the ``sqlite-vec`` extension loaded.
    """
    db_path = get_standards_db_path()
    logger.info("Standards store: SQLite + sqlite-vec at %s", db_path)
    return _connect(db_path)


def reset_connection_cache() -> None:
    """Clear the cached connection (used by tests that need a fresh DB)."""
    get_connection.cache_clear()
