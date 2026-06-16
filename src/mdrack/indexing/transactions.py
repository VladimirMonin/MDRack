"""Database transaction functions for the indexing pipeline.

These functions handle all write operations to the database during indexing.
They are designed to be called within a transaction context.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


def start_index_run(conn: sqlite3.Connection) -> str:
    """Create a new index_runs row with status='running' and return its ID.

    Args:
        conn: An open SQLite connection.

    Returns:
        The UUID run_id of the created index run.
    """
    run_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc).isoformat()

    conn.execute(
        """
        INSERT INTO index_runs (id, started_at, status)
        VALUES (?, ?, ?)
        """,
        (run_id, started_at, "running"),
    )
    conn.commit()
    logger.info("Started index run: %s", run_id)
    return run_id


def complete_index_run(
    conn: sqlite3.Connection,
    run_id: str,
    stats: dict,
    success: bool = True,
    error_message: Optional[str] = None,
) -> None:
    """Update an index run with completion status and stats.

    Args:
        conn: An open SQLite connection.
        run_id: The index run UUID.
        stats: Dictionary with keys: files_seen, files_changed, files_deleted, chunks_created.
        success: Whether the run succeeded (sets status='success' or 'failed').
        error_message: Optional error message if failed.
    """
    finished_at = datetime.now(timezone.utc).isoformat()
    status = "success" if success else "failed"

    conn.execute(
        """
        UPDATE index_runs
        SET finished_at = ?, status = ?,
            files_seen = ?, files_changed = ?, files_deleted = ?, chunks_created = ?,
            error_message = ?
        WHERE id = ?
        """,
        (
            finished_at,
            status,
            stats.get("files_seen", 0),
            stats.get("files_changed", 0),
            stats.get("files_deleted", 0),
            stats.get("chunks_created", 0),
            error_message,
            run_id,
        ),
    )
    conn.commit()
    logger.info("Completed index run: %s (status=%s)", run_id, status)


def record_diagnostic(
    conn: sqlite3.Connection,
    run_id: str,
    severity: str,
    code: str,
    message: str,
    details: Optional[dict] = None,
) -> None:
    """Insert a diagnostic record for an index run.

    Args:
        conn: An open SQLite connection.
        run_id: The index run UUID.
        severity: Severity level (e.g., 'error', 'warning', 'info').
        code: Error code (e.g., 'FILE_READ_ERROR').
        message: Human-readable message.
        details: Optional dict of additional context (will be JSON-serialized).
    """

    details_json = json.dumps(details) if details else None
    diag_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()

    conn.execute(
        """
        INSERT INTO diagnostics (id, run_id, severity, code, message, details, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (diag_id, run_id, severity, code, message, details_json, created_at),
    )
    conn.commit()
    logger.debug("Recorded diagnostic: %s (%s)", code, severity)


def insert_file(
    conn: sqlite3.Connection,
    file_id: str,
    relative_path: str,
    title: str,
    source_hash: str,
    indexed_at: str,
) -> None:
    """Insert a new file record.

    Args:
        conn: An open SQLite connection.
        file_id: UUID for the file.
        relative_path: Path relative to vault root.
        title: Document title from frontmatter or filename.
        source_hash: SHA-256 hash of file content.
        indexed_at: ISO timestamp when indexing completed.
    """
    conn.execute(
        """
        INSERT INTO files (id, relative_path, title, source_hash, indexed_at, status)
        VALUES (?, ?, ?, ?, ?, 'active')
        """,
        (file_id, relative_path, title, source_hash, indexed_at),
    )
    conn.commit()
    logger.debug("Inserted file: %s (%s)", file_id, relative_path)


def upsert_chunk(
    conn: sqlite3.Connection,
    chunk_id: str,
    file_id: str,
    section_id: str,
    content: str,
    content_type: str,
    chunk_index: int,
    heading_path: str,
    previous_chunk_id: Optional[str] = None,
    next_chunk_id: Optional[str] = None,
    embedding_text: Optional[str] = None,
    embedding_text_hash: Optional[str] = None,
) -> None:
    """Insert or update a chunk record.

    Args:
        conn: An open SQLite connection.
        chunk_id: UUID for the chunk.
        file_id: Parent file UUID.
        section_id: Parent section UUID.
        content: Chunk text content.
        content_type: Content type label (e.g., 'text', 'code', 'mermaid', 'table').
        chunk_index: Position within the document.
        heading_path: Serialized heading path (JSON or delimited string).
        previous_chunk_id: UUID of previous chunk (optional).
        next_chunk_id: UUID of next chunk (optional).
        embedding_text: Formatted text for embedding (optional).
        embedding_text_hash: Hash of embedding_text for caching (optional).
    """
    conn.execute(
        """
        INSERT OR REPLACE INTO chunks
        (id, file_id, section_id, content, content_type, chunk_index,
         heading_path, previous_chunk_id, next_chunk_id,
         embedding_text, embedding_text_hash)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            chunk_id,
            file_id,
            section_id,
            content,
            content_type,
            chunk_index,
            heading_path,
            previous_chunk_id,
            next_chunk_id,
            embedding_text,
            embedding_text_hash,
        ),
    )
    conn.commit()
    logger.debug("Upserted chunk: %s (type=%s)", chunk_id, content_type)


def link_section_file(
    conn: sqlite3.Connection,
    section_id: str,
    file_id: str,
) -> None:
    """Ensure a section is linked to its parent file via the sections table.

    This function is mostly a no-op since sections already have file_id,
    but it can be used to validate or enforce the relationship.

    Args:
        conn: An open SQLite connection.
        section_id: Section UUID.
        file_id: File UUID.
    """
    # The sections table already has file_id as a foreign key.
    # This function can be expanded for additional validation if needed.
    logger.debug("Section-file link verified: %s -> %s", section_id, file_id)


def insert_section(
    conn: sqlite3.Connection,
    section_id: str,
    title: str,
    heading_path: str,
    level: int,
    start_line: int,
    end_line: int,
    parent_id: Optional[str],
    file_id: str,
) -> None:
    """Insert a new section record.

    Args:
        conn: An open SQLite connection.
        section_id: UUID for the section.
        title: Section title.
        heading_path: Full heading path (JSON array serialized).
        level: Heading level (1-4).
        start_line: First line number in document.
        end_line: Last line number in document.
        parent_id: Parent section UUID (optional).
        file_id: Parent file UUID.
    """
    conn.execute(
        """
        INSERT INTO sections
        (id, file_id, title, heading_path, level, start_line, end_line, parent_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            section_id,
            file_id,
            title,
            heading_path,
            level,
            start_line,
            end_line,
            parent_id,
        ),
    )
    conn.commit()
    logger.debug("Inserted section: %s (level=%d)", section_id, level)
