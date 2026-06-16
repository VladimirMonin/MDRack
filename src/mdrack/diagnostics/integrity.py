"""Store status and integrity diagnostics."""

from __future__ import annotations

import logging
import sqlite3

from mdrack.storage.sqlite.migrations import get_applied_migrations
from mdrack.storage.sqlite.repositories import (
    count_chunks,
    count_embeddings,
    count_files,
)

logger = logging.getLogger(__name__)


def get_store_status(conn: sqlite3.Connection) -> dict[str, object]:
    """Return a summary of the knowledge store.

    Args:
        conn: An open SQLite connection.

    Returns:
        Dict with files_count, chunks_count, embeddings_count,
        active_profile, and schema_version.
    """
    files_count = count_files(conn)
    chunks_count = count_chunks(conn)
    embeddings_count = count_embeddings(conn)

    applied = get_applied_migrations(conn)
    schema_version = max(applied) if applied else None

    return {
        "files_count": files_count,
        "chunks_count": chunks_count,
        "embeddings_count": embeddings_count,
        "active_profile": "default",
        "schema_version": schema_version,
    }
