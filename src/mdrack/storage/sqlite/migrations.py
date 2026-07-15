"""Custom migration runner for SQLite."""

from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

_MIGRATION_PATTERN = re.compile(r"^(\d{4})_(.+)\.sql$")


class MigrationPlanError(RuntimeError):
    """Migration files or database versions do not form one safe linear history."""


def _migration_plan(migrations_dir: Path) -> list[tuple[str, Path]]:
    sql_files = sorted(migrations_dir.glob("*.sql"))
    plan: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for path in sql_files:
        match = _MIGRATION_PATTERN.fullmatch(path.name)
        if match is None:
            raise MigrationPlanError("migration filename does not match NNNN_name.sql")
        version = match.group(1)
        if version in seen:
            raise MigrationPlanError("duplicate migration version")
        seen.add(version)
        plan.append((version, path))
    expected = [f"{index:04d}" for index in range(len(plan))]
    if [version for version, _ in plan] != expected:
        raise MigrationPlanError("migration versions must be contiguous from 0000")
    return plan


def get_migrations_dir() -> Path:
    """Return the package-local directory containing SQL migrations."""
    return Path(__file__).resolve().with_name("migrations")


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
    plan = _migration_plan(migrations_dir)
    available = {version for version, _ in plan}
    if applied - available:
        raise MigrationPlanError("database contains migration versions unavailable to this build")
    pending = [(version, path) for version, path in plan if version not in applied]

    if not pending:
        logger.info("No pending migrations to apply")
        return

    for version, path in pending:
        logger.info("Applying migration %s from %s", version, path.name)
        sql = path.read_text(encoding="utf-8")
        try:
            conn.executescript(
                "BEGIN IMMEDIATE;\n"
                f"{sql}\n"
                f"INSERT INTO schema_migrations (version) VALUES ('{version}');\n"
                "COMMIT;"
            )
            logger.info("Migration %s applied successfully", version)
        except Exception:
            conn.rollback()
            logger.exception("Migration %s failed, rolled back", version)
            raise
