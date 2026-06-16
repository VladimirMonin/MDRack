"""Pure-Python vector index over the chunk_embeddings table.

Stores embedding vectors as JSON blobs in SQLite and computes cosine
similarity in Python. No external extensions required.
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _norm(a: list[float]) -> float:
    return math.sqrt(sum(x * x for x in a))


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    denom = _norm(a) * _norm(b)
    if denom == 0.0:
        return 0.0
    return _dot(a, b) / denom


class VectorIndex:
    """Manages embedding vectors stored in the ``chunk_embeddings`` table.

    Vectors are serialised as JSON lists of floats and stored in the
    ``embedding`` BLOB column. Similarity search is performed in pure
    Python using cosine similarity.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def upsert(
        self,
        chunk_id: str,
        profile_name: str,
        vector: list[float],
    ) -> None:
        """Insert or replace a vector for the given chunk and profile.

        Args:
            chunk_id: Identifier of the chunk the vector belongs to.
            profile_name: Name of the embedding profile.
            vector: Dense embedding as a list of floats.
        """
        payload = json.dumps(vector).encode("utf-8")
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """
            INSERT INTO chunk_embeddings (chunk_id, profile_name, embedding, embedded_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (chunk_id, profile_name)
            DO UPDATE SET embedding = excluded.embedding,
                         embedded_at = excluded.embedded_at
            """,
            (chunk_id, profile_name, payload, now),
        )
        self._conn.commit()
        logger.debug(
            "Upserted vector for chunk=%s profile=%s dims=%d",
            chunk_id,
            profile_name,
            len(vector),
        )

    def search(
        self,
        query_vector: list[float],
        profile_name: str,
        limit: int = 20,
    ) -> list[dict[str, object]]:
        """Return the *limit* nearest chunks by cosine similarity.

        Args:
            query_vector: The query embedding.
            profile_name: Restrict search to this embedding profile.
            limit: Maximum number of results to return.

        Returns:
            List of dicts ``{"chunk_id": str, "score": float}`` sorted
            by descending similarity.
        """
        rows = self._conn.execute(
            "SELECT chunk_id, embedding FROM chunk_embeddings WHERE profile_name = ?",
            (profile_name,),
        ).fetchall()

        q_norm = _norm(query_vector)
        if q_norm == 0.0:
            return []

        scored: list[dict[str, object]] = []
        for row in rows:
            vec = json.loads(row["embedding"])
            score = _cosine_similarity(query_vector, vec)
            scored.append({"chunk_id": row["chunk_id"], "score": score})

        scored.sort(key=lambda d: d["score"], reverse=True)  # type: ignore[arg-type]
        return scored[:limit]

    def delete(self, chunk_id: str, profile_name: str) -> None:
        """Remove the vector for a single chunk and profile.

        Args:
            chunk_id: Chunk identifier.
            profile_name: Embedding profile name.
        """
        self._conn.execute(
            "DELETE FROM chunk_embeddings WHERE chunk_id = ? AND profile_name = ?",
            (chunk_id, profile_name),
        )
        self._conn.commit()
        logger.debug("Deleted vector chunk=%s profile=%s", chunk_id, profile_name)

    def delete_all(self, profile_name: str) -> int:
        """Remove all vectors for the given profile.

        Args:
            profile_name: Embedding profile name.

        Returns:
            Number of rows deleted.
        """
        cur = self._conn.execute(
            "DELETE FROM chunk_embeddings WHERE profile_name = ?",
            (profile_name,),
        )
        self._conn.commit()
        deleted = cur.rowcount
        logger.debug("Deleted %d vectors for profile=%s", deleted, profile_name)
        return deleted

    def count(self, profile_name: str) -> int:
        """Return the number of stored vectors for the given profile.

        Args:
            profile_name: Embedding profile name.

        Returns:
            Row count.
        """
        row = self._conn.execute(
            "SELECT COUNT(*) AS cnt FROM chunk_embeddings WHERE profile_name = ?",
            (profile_name,),
        ).fetchone()
        return row["cnt"] if row is not None else 0
