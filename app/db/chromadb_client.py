"""
app/db/chromadb_client.py
─────────────────────────────────────────────────────────────────────────────
ChromaDB client for the Spring Design normative-standards vector store.

This module manages the connection to ChromaDB and provides helper functions
to:
  1. Ingest DIN/ASTM standard documents as text embeddings.
  2. Query the collection for relevant normative clauses during compliance
     verification (used by ``compliance_verification_tool`` in production).

The default embedding function is ChromaDB's built-in ``DefaultEmbeddingFunction``
(sentence-transformers all-MiniLM-L6-v2) which runs locally without an API key.
Swap to ``langchain_google_genai.GoogleGenerativeAIEmbeddings`` or similar for
higher-quality retrieval.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

import chromadb
from chromadb.config import Settings as ChromaSettings

from app.core.settings import get_settings

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Client singleton
# ─────────────────────────────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def get_chroma_client() -> chromadb.HttpClient:
    """
    Return a cached ChromaDB HTTP client connected to the configured server.

    Falls back to an in-memory client if the server is unreachable (useful for
    unit tests and environments without a running ChromaDB instance).
    """
    settings = get_settings()
    try:
        client = chromadb.HttpClient(
            host=settings.chroma_host,
            port=settings.chroma_port,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        # Health check
        client.heartbeat()
        logger.info(
            "ChromaDB HTTP client connected: %s:%d",
            settings.chroma_host,
            settings.chroma_port,
        )
        return client
    except Exception as exc:
        logger.warning(
            "ChromaDB server unreachable (%s). Falling back to in-memory client.", exc
        )
        return chromadb.Client()  # type: ignore[return-value]


def get_standards_collection() -> chromadb.Collection:
    """Return (or create) the spring-standards collection."""
    settings = get_settings()
    client = get_chroma_client()
    collection = client.get_or_create_collection(
        name=settings.chroma_collection_standards,
        metadata={"hnsw:space": "cosine"},
    )
    return collection


# ─────────────────────────────────────────────────────────────────────────────
# Ingestion helpers
# ─────────────────────────────────────────────────────────────────────────────

# Representative normative clauses (stub – replace with real PDF-parsed text)
_STANDARD_DOCUMENTS: list[dict[str, Any]] = [
    {
        "id": "DIN2095_1",
        "document": (
            "DIN 2095 §4.1: The spring index C = D/d shall be between 4 and 20 "
            "for compression springs. Values outside this range increase manufacturing difficulty."
        ),
        "metadata": {"standard": "DIN 2095", "section": "4.1", "spring_type": "compression"},
    },
    {
        "id": "DIN2095_2",
        "document": (
            "DIN 2095 §5.3: The slenderness ratio L0/D shall not exceed 5.26 for "
            "springs with fixed-free end conditions to avoid lateral buckling."
        ),
        "metadata": {"standard": "DIN 2095", "section": "5.3", "spring_type": "compression"},
    },
    {
        "id": "DIN2095_3",
        "document": (
            "DIN 2095 §6.1: The corrected shear stress (Wahl factor applied) must "
            "not exceed 45% of the tensile yield strength Sy for static loads, "
            "and 30% of Sy for dynamic (fatigue) loads."
        ),
        "metadata": {"standard": "DIN 2095", "section": "6.1", "spring_type": "compression"},
    },
    {
        "id": "DIN2097_1",
        "document": (
            "DIN 2097 §4.1: Extension springs shall have an initial tension τi "
            "between 0.1 and 0.45 of the corrected shear stress at maximum load."
        ),
        "metadata": {"standard": "DIN 2097", "section": "4.1", "spring_type": "extension"},
    },
    {
        "id": "DIN2194_1",
        "document": (
            "DIN 2194 §3.2: Torsion springs shall be designed such that the bending "
            "stress σb = 32*M / (π*d³) does not exceed the allowable bending stress "
            "σb,allow = 0.7 * Sy."
        ),
        "metadata": {"standard": "DIN 2194", "section": "3.2", "spring_type": "torsion"},
    },
    {
        "id": "ASTM_A125_1",
        "document": (
            "ASTM A125: Heat-treated helical compression and extension springs shall "
            "be stress-relieved at a minimum of 200°C for 20 minutes after coiling "
            "to reduce residual stresses."
        ),
        "metadata": {"standard": "ASTM A125", "section": "general", "spring_type": "compression"},
    },
    {
        "id": "ASTM_F1276_1",
        "document": (
            "ASTM F1276: Spring index C shall be in the range 4–12 for precision "
            "springs used in critical applications."
        ),
        "metadata": {"standard": "ASTM F1276", "section": "general", "spring_type": "compression"},
    },
    {
        "id": "GOODMAN_FATIGUE",
        "document": (
            "Goodman fatigue criterion for springs: (τ_alt / S_es) + (τ_mean / S_sy) ≤ 1/Sf. "
            "Where S_es ≈ 0.324 * Sut (Zimmerli) and S_sy = 0.45 * Sy. "
            "A safety factor Sf ≥ 1.3 is required for dynamic applications."
        ),
        "metadata": {"standard": "Shigley", "section": "fatigue", "spring_type": "all"},
    },
]


def ingest_standards(force: bool = False) -> int:
    """
    Ingest normative standard documents into ChromaDB.

    Args:
        force: If True, delete and re-ingest all documents.

    Returns:
        Number of documents ingested.
    """
    collection = get_standards_collection()

    if force:
        # Clear existing documents
        existing = collection.get()
        if existing["ids"]:
            collection.delete(ids=existing["ids"])
            logger.info("Cleared %d existing standard documents.", len(existing["ids"]))

    # Check what's already there
    existing = collection.get()
    existing_ids = set(existing.get("ids", []))

    to_ingest = [doc for doc in _STANDARD_DOCUMENTS if doc["id"] not in existing_ids]
    if not to_ingest:
        logger.info("All %d standards already ingested. Skipping.", len(_STANDARD_DOCUMENTS))
        return 0

    collection.add(
        ids=[d["id"] for d in to_ingest],
        documents=[d["document"] for d in to_ingest],
        metadatas=[d["metadata"] for d in to_ingest],
    )
    logger.info("Ingested %d standard documents into ChromaDB.", len(to_ingest))
    return len(to_ingest)


# ─────────────────────────────────────────────────────────────────────────────
# Query helpers
# ─────────────────────────────────────────────────────────────────────────────


def query_standards(
    query_text: str,
    spring_type: str = "compression",
    n_results: int = 3,
) -> list[dict[str, Any]]:
    """
    Retrieve the most relevant normative clauses for a given design situation.

    Args:
        query_text:   Natural-language description of the design condition to check.
        spring_type:  Filter by spring type metadata.
        n_results:    Number of top results to return.

    Returns:
        List of dicts with ``document``, ``metadata``, and ``distance`` keys.
    """
    collection = get_standards_collection()

    where_filter: dict[str, Any] = {
        "$or": [
            {"spring_type": {"$eq": spring_type}},
            {"spring_type": {"$eq": "all"}},
        ]
    }

    try:
        results = collection.query(
            query_texts=[query_text],
            n_results=min(n_results, collection.count() or 1),
            where=where_filter,
        )
        output = []
        for i, doc in enumerate(results.get("documents", [[]])[0]):
            output.append({
                "document": doc,
                "metadata": results.get("metadatas", [[]])[0][i],
                "distance": results.get("distances", [[]])[0][i],
            })
        return output
    except Exception as exc:
        logger.warning("ChromaDB query failed: %s. Returning empty results.", exc)
        return []
