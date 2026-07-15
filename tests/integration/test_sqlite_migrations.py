"""Integration tests for SQLite connection and migration runner."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from mdrack.storage.sqlite.connection import get_connection
from mdrack.storage.sqlite.migrations import (
    MigrationPlanError,
    apply_migrations,
    get_applied_migrations,
    get_migrations_dir,
)

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
        assert "assets" in table_names
        assert "asset_references" in table_names
        assert "asset_descriptions" in table_names

        applied = get_applied_migrations(conn)
        assert "0000" in applied
        assert "0001" in applied
        assert "0002" in applied
        assert "0003" in applied
        assert "0004" in applied
        assert "0005" in applied
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
    """After migration, schema_migrations contains all version rows."""
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
        assert {"0000", "0001", "0002", "0003", "0004", "0005"} <= get_applied_migrations(conn)
    finally:
        conn.close()
        db_path.unlink(missing_ok=True)


def test_get_migrations_dir_points_to_sql_files() -> None:
    """The packaged migration directory should resolve to the checked-in SQL files."""
    migrations_dir = get_migrations_dir()

    assert migrations_dir.is_dir()
    assert sorted(path.name for path in migrations_dir.glob("*.sql")) == [
        "0000_schema_migrations.sql",
        "0001_initial.sql",
        "0002_fts.sql",
        "0003_provenance.sql",
        "0004_embedding_profiles.sql",
        "0005_assets.sql",
    ]


def test_0002_database_upgrades_to_0003_and_preserves_legacy_rows(tmp_path: Path) -> None:
    legacy_dir = tmp_path / "legacy-migrations"
    legacy_dir.mkdir()
    for name in ("0000_schema_migrations.sql", "0001_initial.sql", "0002_fts.sql"):
        (legacy_dir / name).write_bytes((MIGRATIONS_DIR / name).read_bytes())
    conn, db_path = _fresh_db()
    try:
        apply_migrations(conn, legacy_dir)
        conn.execute(
            "INSERT INTO files (id, relative_path, title, source_hash, indexed_at) VALUES (?, ?, ?, ?, ?)",
            ("legacy-file", "legacy.md", "Legacy", "hash", "2026-01-01T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO sections (id, file_id, title, level, start_line, end_line) VALUES (?, ?, ?, ?, ?, ?)",
            ("legacy-section", "legacy-file", "Legacy", 2, 1, 2),
        )
        conn.execute(
            "INSERT INTO chunks (id, file_id, section_id, content, chunk_index) VALUES (?, ?, ?, ?, ?)",
            ("legacy-chunk", "legacy-file", "legacy-section", "legacy content", 0),
        )
        conn.commit()

        apply_migrations(conn, MIGRATIONS_DIR)

        row = conn.execute("SELECT id, relative_path, root_id FROM files WHERE id = 'legacy-file'").fetchone()
        assert tuple(row) == ("legacy-file", "legacy.md", "default")
        assert conn.execute("SELECT content FROM chunks WHERE id = 'legacy-chunk'").fetchone()[0] == "legacy content"
        assert "0003" in get_applied_migrations(conn)
        apply_migrations(conn, MIGRATIONS_DIR)
        assert conn.execute("SELECT COUNT(*) FROM files WHERE id = 'legacy-file'").fetchone()[0] == 1
    finally:
        conn.close()
        db_path.unlink(missing_ok=True)


def test_failed_migration_rolls_back_schema_and_version(tmp_path: Path) -> None:
    migrations_dir = tmp_path / "broken"
    migrations_dir.mkdir()
    (migrations_dir / "0000_broken.sql").write_text(
        "CREATE TABLE should_rollback (id INTEGER);\nTHIS IS NOT SQL;\n",
        encoding="utf-8",
    )
    conn, db_path = _fresh_db()
    try:
        with pytest.raises(sqlite3.Error):
            apply_migrations(conn, migrations_dir)
        assert conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='should_rollback'"
        ).fetchone()[0] == 0
        assert "0000" not in get_applied_migrations(conn)
    finally:
        conn.close()
        db_path.unlink(missing_ok=True)


@pytest.mark.parametrize(
    "names",
    [
        ("0000_first.sql", "0000_duplicate.sql"),
        ("0000_first.sql", "0002_gap.sql"),
        ("unexpected.sql",),
    ],
)
def test_invalid_migration_history_fails_before_schema_changes(
    tmp_path: Path,
    names: tuple[str, ...],
) -> None:
    migrations_dir = tmp_path / "invalid"
    migrations_dir.mkdir()
    for name in names:
        (migrations_dir / name).write_text("CREATE TABLE leaked (id INTEGER);", encoding="utf-8")
    conn, db_path = _fresh_db()
    try:
        with pytest.raises(MigrationPlanError):
            apply_migrations(conn, migrations_dir)
        assert conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='leaked'"
        ).fetchone()[0] == 0
    finally:
        conn.close()
        db_path.unlink(missing_ok=True)


def test_database_with_unknown_future_version_fails_closed() -> None:
    conn, db_path = _fresh_db()
    try:
        apply_migrations(conn, MIGRATIONS_DIR)
        conn.execute("INSERT INTO schema_migrations (version) VALUES ('9999')")
        conn.commit()
        with pytest.raises(MigrationPlanError, match="unavailable"):
            apply_migrations(conn, MIGRATIONS_DIR)
    finally:
        conn.close()
        db_path.unlink(missing_ok=True)
