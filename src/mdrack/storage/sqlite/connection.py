"""SQLite connection factory for MDRack."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)


def get_connection(db_path: Path) -> sqlite3.Connection:
    """Create a configured SQLite connection.

    Enables WAL journal mode, foreign keys, and sets row_factory to sqlite3.Row.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        Configured sqlite3.Connection instance.
    """
    logger.debug("storage.sqlite.connection.opened")
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def get_read_only_connection(db_path: Path) -> sqlite3.Connection:
    """Open an existing SQLite database without permitting SQL writes."""
    logger.debug("storage.sqlite.connection.opened_read_only")
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn
