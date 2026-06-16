"""Integration tests for SQLite connection and migration runner."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from mdrack.storage.sqlite.connection import get_connection
from mdrack.storage.sqlite.migrations import apply_migrations, get_applied_migrations

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "src" / "mdrack" / "storage" / "sqlite" / "migrations"


def _fresh_db() -> tuple[sqlite3.Connection, Path]:
    """Create a temporary database file and return a connection plus its path."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db_path = Path(tmp.name)
    conn = get_connection(db_path)
    return conn, db_path


def test_migrations_apply_to_fresh_db() -> None:
    """Applying migrations to a fresh database creates schema_migrations table and initial tables."""
    conn, db_path = _fresh_db()
    try:
        apply_migrations(conn, MIGRATIONS_DIR)

        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        table_names = {row["name"] for row in tables}
        assert "schema_migrations" in table_names
        # Verify initial tables from migration 0001
        assert "files" in table_names
        assert "sections" in table_names
        assert "chunks" in table_names
        assert "embedding_profiles" in table_names
        assert "chunk_embeddings" in table_names
        assert "index_runs" in table_names
        assert "diagnostics" in table_names

        applied = get_applied_migrations(conn)
        assert "0000" in applied
        assert "0001" in applied
    finally:
        conn.close()
        db_path.unlink(missing_ok=True)


def test_migrations_are_idempotent() -> None:
    """Applying migrations twice does not fail and does not duplicate records."""
    conn, db_path = _fresh_db()
    try:
        apply_migrations(conn, MIGRATIONS_DIR)
        applied_first = get_applied_migrations(conn)

        apply_migrations(conn, MIGRATIONS_DIR)
        applied_second = get_applied_migrations(conn)

        assert applied_first == applied_second

        count = conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
        assert count == len(applied_second)
    finally:
        conn.close()
        db_path.unlink(missing_ok=True)


def test_schema_migrations_table_populated() -> None:
    """After migration, schema_migrations contains both version rows."""
    conn, db_path = _fresh_db()
    try:
        apply_migrations(conn, MIGRATIONS_DIR)

        row_0000 = conn.execute(
            "SELECT version, applied_at FROM schema_migrations WHERE version = '0000'"
        ).fetchone()
        assert row_0000 is not None
        assert row_0000["version"] == "0000"
        assert row_0000["applied_at"] is not None

        row_0001 = conn.execute(
            "SELECT version, applied_at FROM schema_migrations WHERE version = '0001'"
        ).fetchone()
        assert row_0001 is not None
        assert row_0001["version"] == "0001"
        assert row_0001["applied_at"] is not None
    finally:
        conn.close()
        db_path.unlink(missing_ok=True)
