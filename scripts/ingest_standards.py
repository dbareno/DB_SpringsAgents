"""
scripts/ingest_standards.py
─────────────────────────────────────────────────────────────────────────────
Extract text from DIN/BS EN standard PDFs in docs/ and ingest them into
ChromaDB as structured normative clauses.

Usage:
    python -m scripts.ingest_standards          # Ingest new chunks only
    python -m scripts.ingest_standards --force  # Re-ingest everything
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import re
import sys
from pathlib import Path
from typing import Any

# Ensure project root is on path when running as a script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pypdf import PdfReader

from app.db.chromadb_client import ingest_standards, get_standards_collection_stats

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# PDF source directory
# ─────────────────────────────────────────────────────────────────────────────

DOCS_DIR = Path(__file__).resolve().parent.parent / "docs"

# ─────────────────────────────────────────────────────────────────────────────
# Spring-type inference per filename  (heuristic — expanded as needed)
# ─────────────────────────────────────────────────────────────────────────────

FILENAME_SPRING_TYPE: dict[str, str] = {
    "393310294-BS-EN-13906-1-2013.pdf": "compression",
    "BS-EN-10089-for-Hot-Rolled-Steel-of-Quenched-and-Tem.pdf": "compression",
    "BS-EN-ISO-26909-2010-en.pdf": "all",
    "DIN EN 10270 - 1 Steel wire for mechanical springs.pdf": "all",
    "DIN EN 10270 - 2 Steel wire for mechanical springs.pdf": "all",
    "DIN EN 10270 - 3 Steel wire for mechanical springs.pdf": "all",
}

# Fallback: keyword detection within the extracted text
_SPRING_TYPE_KEYWORDS: dict[str, list[str]] = {
    "compression": [
        "compression spring", "compression springs",
        "helical compression", "buckling", "slenderness",
    ],
    "extension": [
        "extension spring", "extension springs",
        "initial tension", "tensile spring",
    ],
    "torsion": [
        "torsion spring", "torsion springs",
        "torsional", "bending stress",
    ],
}


def _normalise_standard_name(filename: str) -> str:
    """Return a clean standard name from the PDF filename."""
    name = filename.replace(".pdf", "").strip()
    # Trim leading numbers like "393310294-"
    name = re.sub(r"^\d+-", "", name)
    return name


def _infer_spring_type(filename: str, text_sample: str) -> str:
    """
    Infer the spring type for a given PDF.

    Priority: explicit filename mapping → keyword match in text → "all".
    """
    # 1. Explicit filename mapping
    inferred = FILENAME_SPRING_TYPE.get(filename)
    if inferred:
        return inferred

    # 2. Keyword search in the first 2000 chars
    sample = text_sample[:2000].lower()
    for stype, keywords in _SPRING_TYPE_KEYWORDS.items():
        if any(kw in sample for kw in keywords):
            return stype

    # 3. Fallback
    return "all"


# ─────────────────────────────────────────────────────────────────────────────
# Chunking
# ─────────────────────────────────────────────────────────────────────────────

# Patterns that signal the start of a new normative section
_SECTION_PATTERNS: list[str] = [
    r"^\s*§\s*\d",                        # German-style "§ 5.3"
    r"^\s*(?:Clause|Section|Annex)\s+\d",  # English-style "Clause 4", "Section 7"
    r"^\s*\d+\.\d+(?:\s{2,}|\))",          # "5.1  Title" or "5.1) Title"
    r"^\s*\d+\s{2,}[A-Z]",                 # "5  Title" (number + spaced text)
    r"^\s*Table\s+\d",                     # Table headers
    r"^\s*Figure\s+\d",                    # Figure captions
    r"^\s*Annex\s+[A-Z]",                  # "Annex A", "Annex B"
]


def _is_section_boundary(line: str) -> bool:
    """Return True if *line* looks like the start of a new normative section."""
    stripped = line.strip()
    if not stripped:
        return False
    # Skip page numbers and headers/footers (short numeric-only lines)
    if re.match(r"^\d+$", stripped) and len(stripped) <= 4:
        return False
    for pattern in _SECTION_PATTERNS:
        if re.match(pattern, stripped):
            return True
    return False


def _chunk_pdf_text(
    full_text: str,
    standard: str,
    page_map: dict[int, int],  # char_offset → page_number
) -> list[dict[str, Any]]:
    """
    Split extracted PDF text into meaningful normative clause chunks.

    Args:
        full_text:  The complete extracted text of the PDF.
        standard:   Clean standard name (for metadata).
        page_map:   Mapping of character offset → page number.

    Returns:
        List of chunk dicts with keys ``id``, ``document``, ``metadata``.
    """
    lines = full_text.split("\n")
    chunks_raw: list[dict[str, Any]] = []
    current_lines: list[str] = []
    current_section: str = "general"
    current_page: int = 1

    for line in lines:
        # Detect page number from the page_map (use approximate offset)
        char_offset = len("\n".join(current_lines))
        # Find the nearest page map entry
        page_candidates = [
            p for off, p in sorted(page_map.items()) if off <= char_offset
        ]
        if page_candidates:
            current_page = page_candidates[-1]

        if _is_section_boundary(line):
            # Save previous chunk
            text = "\n".join(current_lines).strip()
            if text and len(text) >= 50:
                chunks_raw.append({
                    "text": text,
                    "section": current_section,
                    "page": current_page,
                })
            # Start new section
            current_lines = [line]
            # Extract section number from the line
            section_match = re.match(
                r"[\s§]*(?:Clause|Section|Annex\s+)?(\d+(?:\.\d+)*)",
                line.strip(),
            )
            current_section = (
                section_match.group(1) if section_match else "general"
            )
        else:
            current_lines.append(line)

    # Last chunk
    text = "\n".join(current_lines).strip()
    if text and len(text) >= 50:
        chunks_raw.append({
            "text": text,
            "section": current_section,
            "page": current_page,
        })

    # ── Post-process: merge tiny chunks, split oversized ones ────────────
    return _postprocess_chunks(chunks_raw, standard)


def _postprocess_chunks(
    chunks_raw: list[dict[str, Any]],
    standard: str,
) -> list[dict[str, Any]]:
    """Merge adjacent chunks under 200 chars; split chunks over 1000 chars."""
    # Step 1: merge tiny neighbours
    merged: list[dict[str, Any]] = []
    for chunk in chunks_raw:
        if not merged:
            merged.append(chunk)
            continue
        if len(chunk["text"]) < 200 and len(merged[-1]["text"]) < 1000:
            # Merge into the previous chunk
            merged[-1]["text"] += "\n" + chunk["text"]
            merged[-1]["section"] = (
                merged[-1]["section"]
                if len(merged[-1]["text"]) < 1000
                else chunk["section"]
            )
        else:
            merged.append(chunk)

    # Step 2: split oversized chunks (~1000+ chars) into sentence-bounded pieces
    final: list[dict[str, Any]] = []
    for chunk in merged:
        if len(chunk["text"]) > 1000:
            sentences = re.split(r"(?<=[.?!])\s+", chunk["text"])
            buffer = ""
            for sent in sentences:
                if len(buffer) + len(sent) > 900 and buffer:
                    final.append({
                        "text": buffer.strip(),
                        "section": chunk["section"],
                        "page": chunk["page"],
                    })
                    buffer = sent
                else:
                    buffer += (" " if buffer else "") + sent
            if buffer:
                final.append({
                    "text": buffer.strip(),
                    "section": chunk["section"],
                    "page": chunk["page"],
                })
        else:
            if chunk["text"].strip():
                final.append(chunk)

    # Step 3: build ChromaDB document dicts
    result: list[dict[str, Any]] = []
    for i, chunk in enumerate(final):
        chunk_id = hashlib.md5(
            f"{standard}|{chunk['section']}|{i}".encode()
        ).hexdigest()[:16]

        # Determine spring_type from the full chunk text (override with "all"
        # if the standard itself covers multiple types)
        st = _infer_spring_type("", chunk["text"])

        result.append({
            "id": chunk_id,
            "document": chunk["text"],
            "metadata": {
                "standard": standard,
                "section": chunk["section"],
                "page": chunk["page"],
                "spring_type": st,
            },
        })

    return result


# ─────────────────────────────────────────────────────────────────────────────
# PDF extraction
# ─────────────────────────────────────────────────────────────────────────────


def extract_pdf(
    pdf_path: Path,
) -> tuple[str, dict[int, int]]:
    """
    Extract text from a single PDF file.

    Args:
        pdf_path: Path to the PDF file.

    Returns:
        Tuple of ``(full_text, page_map)`` where ``page_map`` maps
        character offset → 1-based page number.
    """
    reader = PdfReader(str(pdf_path))
    pages: list[str] = []
    page_map: dict[int, int] = {}
    offset = 0
    for page_num, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        pages.append(text)
        page_map[offset] = page_num
        offset += len(text)
    full_text = "\n".join(pages)
    return full_text, page_map


# ─────────────────────────────────────────────────────────────────────────────
# Per-PDF processing
# ─────────────────────────────────────────────────────────────────────────────


def process_pdf(pdf_path: Path) -> list[dict[str, Any]]:
    """
    Process a single PDF: extract text, chunk, and return ChromaDB-ready docs.

    Args:
        pdf_path: Absolute path to the PDF file.

    Returns:
        List of ChromaDB document dicts. Empty list if PDF is scanned/unreadable.
    """
    filename = pdf_path.name
    standard = _normalise_standard_name(filename)
    logger.info("Processing: %s  (%s)", filename, standard)

    full_text, page_map = extract_pdf(pdf_path)
    if not full_text.strip():
        logger.warning(
            "  ⚠ PDF appears to be scanned (no extractable text). Skipping."
        )
        return []

    # Infer spring type from the full text
    spring_type = _infer_spring_type(filename, full_text)
    logger.info(
        "  Extracted %d chars over %d pages | spring_type=%s",
        len(full_text),
        len(page_map),
        spring_type,
    )

    return _chunk_pdf_text(full_text, standard, page_map)


# ─────────────────────────────────────────────────────────────────────────────
# Main entrypoint
# ─────────────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest DIN/BS EN standard PDFs into ChromaDB."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-ingest everything (delete existing docs first).",
    )
    args = parser.parse_args()

    pdf_files = sorted(DOCS_DIR.glob("*.pdf"))
    if not pdf_files:
        logger.error("No PDF files found in %s", DOCS_DIR)
        sys.exit(1)

    logger.info("Found %d PDF(s) in %s", len(pdf_files), DOCS_DIR)

    all_chunks: list[dict[str, Any]] = []
    per_pdf: dict[str, int] = {}

    for pdf_path in pdf_files:
        chunks = process_pdf(pdf_path)
        if chunks:
            all_chunks.extend(chunks)
            per_pdf[pdf_path.name] = len(chunks)
        else:
            per_pdf[pdf_path.name] = 0

    total_before = len(all_chunks)
    logger.info(
        "Total chunks prepared: %d across %d PDF(s)",
        total_before,
        len(per_pdf),
    )

    # Ingest into ChromaDB
    ingested = ingest_standards(documents=all_chunks, force=args.force)

    # Report (use ASCII-safe characters for Windows console)
    print("\n" + "=" * 60)
    print("INGESTION SUMMARY")
    print("=" * 60)
    for filename, count in per_pdf.items():
        status = "[OK]" if count > 0 else "[SKIP]"
        print(f"  {status} {filename:65s} {count:4d} chunks")
    print(f"\n  Total new chunks ingested: {ingested}")
    print(f"  Total unique chunks in collection: {total_before}")

    # Collection stats
    stats = get_standards_collection_stats()
    print(f"\n  Collection total documents: {stats['total_documents']}")
    print(f"  By standard: {stats['by_standard']}")
    print(f"  By spring type: {stats['by_spring_type']}")
    print("=" * 60)


if __name__ == "__main__":
    main()
