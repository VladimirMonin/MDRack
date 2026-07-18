"""FTS5 full-text search operations for chunks."""

from __future__ import annotations

import logging
import sqlite3

from mdrack_sqlite.fts import plain_query_fallback

logger = logging.getLogger(__name__)

class FTSQueryError(Exception):
    """Raised when an FTS5 query is invalid."""


def upsert_fts(
    conn: sqlite3.Connection,
    chunk_id: str,
    content: str,
    content_type: str,
    heading_path: str,
) -> None:
    """Insert or replace a row in chunks_fts.

    Args:
        conn: An open SQLite connection.
        chunk_id: Primary key of the chunk in the chunks table.
        content: Chunk text content to index.
        content_type: Content type label (stored, not indexed).
        heading_path: Heading path for context (indexed).
    """
    conn.execute(
        "DELETE FROM chunks_fts WHERE chunk_id = ?",
        (chunk_id,),
    )
    conn.execute(
        """
        INSERT INTO chunks_fts (chunk_id, content, content_type, heading_path)
        VALUES (?, ?, ?, ?)
        """,
        (chunk_id, content, content_type, heading_path),
    )
    conn.commit()
    logger.debug("Upserted FTS entry for chunk %s", chunk_id)


def delete_fts(conn: sqlite3.Connection, chunk_id: str) -> None:
    """Delete a row from chunks_fts by chunk_id.

    Args:
        conn: An open SQLite connection.
        chunk_id: The chunk ID to remove from the index.
    """
    conn.execute(
        "DELETE FROM chunks_fts WHERE chunk_id = ?",
        (chunk_id,),
    )
    conn.commit()
    logger.debug("Deleted FTS entry for chunk %s", chunk_id)


def search_fts(
    conn: sqlite3.Connection,
    query: str,
    limit: int = 20,
) -> list[dict]:
    """Search chunks_fts using FTS5 full-text search.

    Args:
        conn: An open SQLite connection.
        query: FTS5 query string.
        limit: Maximum number of results to return.

    Returns:
        List of dicts with keys: chunk_id, rank, snippet.

    Raises:
        FTSQueryError: If the query is invalid or search fails.
    """
    if not query.strip():
        raise FTSQueryError("Search query must not be empty")

    try:
        cursor = conn.execute(
            """
            SELECT chunk_id, rank, snippet(chunks_fts, 1, '<b>', '</b>', '...', 64) AS snippet
            FROM chunks_fts
            WHERE chunks_fts MATCH ?
            ORDER BY rank, chunks_fts.rowid
            LIMIT ?
            """,
            (query, limit),
        )
        return [
            {
                "chunk_id": row["chunk_id"],
                "rank": row["rank"],
                "snippet": row["snippet"],
            }
            for row in cursor.fetchall()
        ]
    except sqlite3.OperationalError as exc:
        fallback_query = plain_query_fallback(query)
        if fallback_query is not None:
            try:
                cursor = conn.execute(
                    """
                    SELECT chunk_id, rank, snippet(chunks_fts, 1, '<b>', '</b>', '...', 64) AS snippet
                    FROM chunks_fts
                    WHERE chunks_fts MATCH ?
                    ORDER BY rank, chunks_fts.rowid
                    LIMIT ?
                    """,
                    (fallback_query, limit),
                )
                return [
                    {
                        "chunk_id": row["chunk_id"],
                        "rank": row["rank"],
                        "snippet": row["snippet"],
                    }
                    for row in cursor.fetchall()
                ]
            except sqlite3.OperationalError:
                pass
        raise FTSQueryError(f"Invalid FTS query: {exc}") from exc


def rebuild_fts(conn: sqlite3.Connection) -> None:
    """Rebuild the FTS index from the chunks table.

    This deletes all rows in chunks_fts and re-inserts from chunks.
    Useful after bulk operations or corruption recovery.
    """
    conn.execute("DELETE FROM chunks_fts")
    conn.execute(
        """
        INSERT INTO chunks_fts (chunk_id, content, content_type, heading_path)
        SELECT id, content, content_type, heading_path FROM chunks
        """,
    )
    conn.commit()
    logger.info("FTS index rebuilt from chunks table")
