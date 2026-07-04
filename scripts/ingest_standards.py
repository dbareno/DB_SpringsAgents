"""
scripts/ingest_standards.py
─────────────────────────────────────────────────────────────────────────────
Ingest DIN/ASTM standards documents (PDF or plain text) from
``data/standards/`` into the offline sqlite-vec standards store.

Replaces the previous ChromaDB-backed ingester — ChromaDB's runtime
dependencies (``onnxruntime`` locally, ``chromadb.telemetry.product.posthog``
when frozen) made the store unusable both in dev and inside the packaged
``.exe``. See ``app/standards/`` for the new store, embedder, and retrieval
implementation.

Idempotent: standards already present (by ``standard_name``, derived from
the file stem) are skipped unless ``--force`` is passed.

Usage:
    python -m scripts.ingest_standards          # Ingest new documents only
    python -m scripts.ingest_standards --force  # Re-ingest everything

Hooked into ``app/main.py``'s FastAPI lifespan (mirrors
``scripts/seed_materials.py``) so the packaged ``.exe`` self-ingests its
bundled starter standards on first launch.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Ensure project root is on path when running as a script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.standards.ingestion import DEFAULT_STANDARDS_DIR, ingest_directory

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)


def ingest(force: bool = False) -> dict[str, int]:
    """
    Ingest every standards document under ``data/standards/`` idempotently.

    Args:
        force: Re-ingest everything, even standards already present.

    Returns:
        Dict mapping standard name -> number of chunks inserted (0 for
        standards that were already ingested and skipped).
    """
    results = ingest_directory(force=force)

    print("\n" + "=" * 60)
    print("INGESTION SUMMARY")
    print("=" * 60)
    if not results:
        print(f"  [SKIP] No standards documents found in {DEFAULT_STANDARDS_DIR}")
    for name, count in results.items():
        status = "[OK]" if count > 0 else "[SKIP]"
        print(f"  {status} {name:40s} {count:4d} chunks")
    total = sum(results.values())
    print(f"\n  Total new chunks ingested: {total}")
    print("=" * 60)

    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest DIN/ASTM standards documents into the sqlite-vec store."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-ingest everything (delete existing chunks per standard first).",
    )
    args = parser.parse_args()
    ingest(force=args.force)


if __name__ == "__main__":
    main()
