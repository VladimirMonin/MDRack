"""Repository queries for files, sections, chunks, and embeddings."""

from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger(__name__)


def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
    """Convert a sqlite3.Row to a plain dict, or return None."""
    if row is None:
        return None
    return dict(row)


def list_files(conn: sqlite3.Connection, offset: int = 0, limit: int = 20) -> list[dict]:
    """Return a paginated list of files ordered by relative_path.

    Args:
        conn: An open SQLite connection.
        offset: Number of rows to skip.
        limit: Maximum number of rows to return.

    Returns:
        List of file dicts.
    """
    cursor = conn.execute(
        "SELECT * FROM files ORDER BY relative_path LIMIT ? OFFSET ?",
        (limit, offset),
    )
    return [dict(row) for row in cursor.fetchall()]


def get_file(conn: sqlite3.Connection, file_id: str) -> dict | None:
    """Return a single file by its ID, or None if not found.

    Args:
        conn: An open SQLite connection.
        file_id: The file UUID.

    Returns:
        File dict or None.
    """
    cursor = conn.execute("SELECT * FROM files WHERE id = ?", (file_id,))
    return _row_to_dict(cursor.fetchone())


def get_file_by_path(conn: sqlite3.Connection, relative_path: str) -> dict | None:
    """Return a single file by its relative path, or None if not found.

    Args:
        conn: An open SQLite connection.
        relative_path: The file's relative path in the vault.

    Returns:
        File dict or None.
    """
    cursor = conn.execute(
        "SELECT * FROM files WHERE relative_path = ?",
        (relative_path,),
    )
    return _row_to_dict(cursor.fetchone())


def list_sections(conn: sqlite3.Connection, file_id: str) -> list[dict]:
    """Return all sections for a file, ordered by start_line.

    Args:
        conn: An open SQLite connection.
        file_id: The file UUID.

    Returns:
        List of section dicts.
    """
    cursor = conn.execute(
        "SELECT * FROM sections WHERE file_id = ? ORDER BY start_line",
        (file_id,),
    )
    return [dict(row) for row in cursor.fetchall()]


def list_chunks_by_section(conn: sqlite3.Connection, section_id: str) -> list[dict]:
    """Return all chunks for a section, ordered by chunk_index.

    Args:
        conn: An open SQLite connection.
        section_id: The section UUID.

    Returns:
        List of chunk dicts.
    """
    cursor = conn.execute(
        "SELECT * FROM chunks WHERE section_id = ? ORDER BY chunk_index",
        (section_id,),
    )
    return [dict(row) for row in cursor.fetchall()]


def get_section(conn: sqlite3.Connection, section_id: str) -> dict | None:
    """Return a single section by its ID, or None if not found.

    Args:
        conn: An open SQLite connection.
        section_id: The section UUID.

    Returns:
        Section dict or None.
    """
    cursor = conn.execute("SELECT * FROM sections WHERE id = ?", (section_id,))
    return _row_to_dict(cursor.fetchone())


def get_chunk(conn: sqlite3.Connection, chunk_id: str) -> dict | None:
    """Return a single chunk by its ID, or None if not found.

    Args:
        conn: An open SQLite connection.
        chunk_id: The chunk UUID.

    Returns:
        Chunk dict or None.
    """
    cursor = conn.execute("SELECT * FROM chunks WHERE id = ?", (chunk_id,))
    return _row_to_dict(cursor.fetchone())


def get_neighbors(conn: sqlite3.Connection, chunk_id: str, count: int = 2) -> list[dict]:
    """Return previous and next chunks relative to the given chunk.

    Uses the ``previous_chunk_id`` and ``next_chunk_id`` columns in the
    chunks table to walk the linked list in both directions.  Results are
    ordered so that earlier chunks come first (i.e. previous chunks in
    ``count``..1 order, then next chunks in 1..``count`` order).

    Args:
        conn: An open SQLite connection.
        chunk_id: The anchor chunk UUID.
        count: Number of neighbours to retrieve in each direction.

    Returns:
        List of neighbouring chunk dicts, ordered by chunk_index.
    """
    anchor = get_chunk(conn, chunk_id)
    if anchor is None:
        return []

    prev_chunks: list[dict] = []
    current_id: str | None = anchor.get("previous_chunk_id")
    for _ in range(count):
        if current_id is None:
            break
        chunk = get_chunk(conn, current_id)
        if chunk is None:
            break
        prev_chunks.append(chunk)
        current_id = chunk.get("previous_chunk_id")

    next_chunks: list[dict] = []
    current_id = anchor.get("next_chunk_id")
    for _ in range(count):
        if current_id is None:
            break
        chunk = get_chunk(conn, current_id)
        if chunk is None:
            break
        next_chunks.append(chunk)
        current_id = chunk.get("next_chunk_id")

    prev_chunks.reverse()
    return prev_chunks + next_chunks


def count_files(conn: sqlite3.Connection) -> int:
    """Return the total number of files.

    Args:
        conn: An open SQLite connection.

    Returns:
        Row count.
    """
    return conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]


def count_chunks(conn: sqlite3.Connection) -> int:
    """Return the total number of chunks.

    Args:
        conn: An open SQLite connection.

    Returns:
        Row count.
    """
    return conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]


def count_embeddings(conn: sqlite3.Connection, profile_name: str = "default") -> int:
    """Return the number of embeddings for a given profile.

    Args:
        conn: An open SQLite connection.
        profile_name: Embedding profile to count.

    Returns:
        Row count.
    """
    return conn.execute(
        "SELECT COUNT(*) FROM chunk_embeddings WHERE profile_name = ?",
        (profile_name,),
    ).fetchone()[0]
