"""Custom migration runner for SQLite."""

from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

_MIGRATION_PATTERN = re.compile(r"^(\d{4})_(.+)\.sql$")


def _ensure_schema_migrations(conn: sqlite3.Connection) -> None:
    """Create the schema_migrations table if it does not exist."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.commit()


def get_applied_migrations(conn: sqlite3.Connection) -> set[str]:
    """Return the set of migration versions already applied.

    Args:
        conn: An open SQLite connection.

    Returns:
        Set of version strings (e.g., {"0000", "0001"}).
    """
    _ensure_schema_migrations(conn)
    cursor = conn.execute("SELECT version FROM schema_migrations")
    return {row["version"] for row in cursor.fetchall()}


def apply_migrations(conn: sqlite3.Connection, migrations_dir: Path) -> None:
    """Read and apply pending .sql migration files in version order.

    Migration files must be named ``NNNN_name.sql`` (e.g., 0001_initial.sql).
    Each migration runs inside its own transaction and is recorded in
    ``schema_migrations`` after successful application.

    Args:
        conn: An open SQLite connection.
        migrations_dir: Directory containing .sql migration files.
    """
    _ensure_schema_migrations(conn)
    applied = get_applied_migrations(conn)

    sql_files = sorted(migrations_dir.glob("*.sql"))
    pending: list[tuple[str, Path]] = []
    for path in sql_files:
        match = _MIGRATION_PATTERN.match(path.name)
        if match:
            version = match.group(1)
            if version not in applied:
                pending.append((version, path))

    if not pending:
        logger.info("No pending migrations to apply")
        return

    for version, path in pending:
        logger.info("Applying migration %s from %s", version, path.name)
        sql = path.read_text(encoding="utf-8")
        try:
            conn.executescript(sql)
            conn.execute(
                "INSERT INTO schema_migrations (version) VALUES (?)",
                (version,),
            )
            conn.commit()
            logger.info("Migration %s applied successfully", version)
        except Exception:
            conn.rollback()
            logger.exception("Migration %s failed, rolled back", version)
            raise
