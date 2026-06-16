"""Hashing utilities for embedding text deduplication."""

from __future__ import annotations

import hashlib


def hash_embedding_text(text: str) -> str:
    """Return the SHA-256 hex digest of *text*.

    Used to detect when re-embedding is needed: if the embedding text
    hasn't changed, the existing vector can be reused.

    Parameters
    ----------
    text:
        The embedding text string to hash.

    Returns
    -------
    str
        64-character lowercase hex SHA-256 digest.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
