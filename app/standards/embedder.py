"""
app/standards/embedder.py
─────────────────────────────────────────────────────────────────────────────
Offline, dependency-free text embedder for the standards RAG store.

Design decision (deviation from the original ADR-3 spike request)
───────────────────────────────────────────────────────────────────────────
The initial plan called for ``sentence-transformers`` (``all-MiniLM-L6-v2``).
In practice that package pulls in ``torch`` + ``transformers`` +
``huggingface-hub`` (multi-gigabyte, native-wheel heavy dependency tree) and
commonly drags in an ONNX/tokenizers backend — i.e. it reintroduces the exact
class of fragile, hard-to-freeze native dependency that broke ChromaDB
(``onnxruntime`` DLL / ``posthog`` submodule) in the first place. That is not
"tiny" or safely offline-packageable for PyInstaller.

Instead this module implements a **deterministic hashing-trick bag-of-words
embedder**: no ML framework, no model download, no native deps beyond
``numpy`` (already a hard dependency of this project). It is:

* Deterministic — the same text always produces the same vector (required by
  the ADR-3 acceptance test and by ``retrieve_standards`` reproducibility).
* Offline — pure Python + numpy, nothing to download, nothing to freeze.
* Small — no model weights, negligible import cost, trivially bundled by
  PyInstaller.

It is intentionally lower quality than a learned sentence embedding, but for
this corpus (short, keyword-dense normative clauses) cosine similarity over
hashed term vectors is sufficient to surface the right clauses, and it fully
unblocks P2-3 without the packaging risk. If retrieval quality ever becomes
the limiting factor, swap this module for a proper local embedding model
that has been validated to freeze cleanly in the ``.exe`` first.
"""

from __future__ import annotations

import hashlib
import re
from functools import lru_cache

import numpy as np

# Fixed output dimensionality. 384 keeps parity with the originally proposed
# all-MiniLM-L6-v2 vector size so downstream consumers/tests can assume a
# stable shape regardless of embedder implementation.
EMBEDDING_DIM = 384

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    """Lowercase + extract alphanumeric tokens (unigrams and bigrams)."""
    tokens = _TOKEN_RE.findall(text.lower())
    if not tokens:
        return []
    bigrams = [f"{a}_{b}" for a, b in zip(tokens, tokens[1:])]
    return tokens + bigrams


def _token_bucket(token: str, dim: int) -> tuple[int, float]:
    """
    Deterministically hash a token to a (bucket_index, sign) pair using the
    hashing trick (Weinberger et al.). Using a real hash function (not
    Python's salted ``hash()``) guarantees the same token always maps to the
    same bucket across processes and runs.
    """
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    value = int.from_bytes(digest, byteorder="big", signed=False)
    bucket = value % dim
    sign = 1.0 if (value // dim) % 2 == 0 else -1.0
    return bucket, sign


@lru_cache(maxsize=4096)
def embed_text(text: str) -> list[float]:
    """
    Deterministically embed ``text`` into a fixed-size ``EMBEDDING_DIM``
    vector using a hashing-trick bag-of-words representation, L2-normalized.

    The same input string always returns the same vector (memoized and
    deterministic by construction), which is required for reproducible
    retrieval and is asserted directly in the test suite.

    Args:
        text: Arbitrary input text (query or corpus chunk).

    Returns:
        A list of ``EMBEDDING_DIM`` floats, L2-normalized. An all-zero vector
        is returned for empty/whitespace-only input.
    """
    vector = np.zeros(EMBEDDING_DIM, dtype=np.float32)
    tokens = _tokenize(text)

    for token in tokens:
        bucket, sign = _token_bucket(token, EMBEDDING_DIM)
        vector[bucket] += sign

    norm = float(np.linalg.norm(vector))
    if norm > 0.0:
        vector = vector / norm

    return vector.tolist()
