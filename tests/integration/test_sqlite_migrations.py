"""Integration tests for SQLite connection and migration runner."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import tempfile
from pathlib import Path

import pytest
from click.testing import CliRunner

import mdrack.storage.sqlite.migrations as migration_module
from mdrack.adapters.sqlite.index_storage import SQLiteIndexStorage
from mdrack.application.retrieval import RetrievalService
from mdrack.cli import main
from mdrack.storage.sqlite.connection import get_connection
from mdrack.storage.sqlite.migrations import (
    EXPECTED_MIGRATION_MANIFEST,
    EXPECTED_MIGRATION_MANIFEST_DIGEST,
    EXPECTED_MIGRATION_VERSION,
    MigrationPlanError,
    _framed_manifest_digest,
    apply_migrations,
    get_applied_migrations,
    get_migrations_dir,
)

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "src" / "mdrack" / "storage" / "sqlite" / "migrations"


def _apply_migration_prefix(conn: sqlite3.Connection, names: tuple[str, ...]) -> None:
    """Build a historical fixture without weakening production package validation."""
    for name in names:
        version = name[:4]
        conn.executescript((MIGRATIONS_DIR / name).read_text(encoding="utf-8"))
        conn.execute("INSERT INTO schema_migrations (version) VALUES (?)", (version,))
    conn.commit()


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
        assert "0006" in applied
        chunk_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(chunks)").fetchall()
        }
        assert {"start_offset", "end_offset", "block_kind", "chunk_kind"} <= chunk_columns
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
        assert {"0000", "0001", "0002", "0003", "0004", "0005", "0006"} <= get_applied_migrations(conn)
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
        "0006_complete_provenance.sql",
    ]


def test_0002_database_upgrades_to_0003_and_preserves_legacy_rows() -> None:
    conn, db_path = _fresh_db()
    try:
        _apply_migration_prefix(
            conn,
            ("0000_schema_migrations.sql", "0001_initial.sql", "0002_fts.sql"),
        )
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


def test_0005_database_upgrades_with_defensible_public_provenance(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    store = root / ".mdrack"
    store.mkdir(parents=True)
    db_path = store / "knowledge.db"
    conn = get_connection(db_path)
    try:
        _apply_migration_prefix(
            conn,
            tuple(path.name for path in sorted(MIGRATIONS_DIR.glob("*.sql")) if not path.name.startswith("0006_")),
        )
        conn.execute(
            "INSERT INTO files "
            "(id, logical_id, root_id, relative_path, title, source_hash, indexed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("record-file", "file-logical", "legacy", "legacy.md", "Legacy", "hash", "2026-01-01"),
        )
        conn.execute(
            "INSERT INTO sections "
            "(id, logical_id, file_id, title, heading_path, level, start_line, end_line) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "record-section",
                "section-logical",
                "record-file",
                "Legacy",
                '["Legacy"]',
                1,
                1,
                4,
            ),
        )
        for index, (record_id, logical_id, content, content_type) in enumerate(
            (
                ("record-text", "chunk-text-logical", "legacy prose sentinel", "text"),
                ("record-code", "chunk-code-logical", "legacy code sentinel", "code"),
            )
        ):
            conn.execute(
                "INSERT INTO chunks "
                "(id, logical_id, file_id, section_id, content, content_type, chunk_index, "
                "heading_path, start_line, end_line, block_logical_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record_id,
                    logical_id,
                    "record-file",
                    "record-section",
                    content,
                    content_type,
                    index,
                    '["Legacy"]',
                    index + 1,
                    index + 1,
                    f"block-{content_type}-logical",
                ),
            )
            conn.execute(
                "INSERT INTO chunks_fts (chunk_id, content, content_type, heading_path) "
                "VALUES (?, ?, ?, ?)",
                (record_id, content, content_type, "Legacy"),
            )
        conn.commit()

        apply_migrations(conn, MIGRATIONS_DIR)

        migrated = conn.execute(
            "SELECT logical_id, block_kind, chunk_kind FROM chunks ORDER BY chunk_index"
        ).fetchall()
        assert [tuple(row) for row in migrated] == [
            ("chunk-text-logical", "paragraph", "text"),
            ("chunk-code-logical", "code", "code"),
        ]

        storage = SQLiteIndexStorage(conn)
        retrieval = RetrievalService(storage)
        for logical_id, query, expected_kind in (
            ("chunk-text-logical", "prose", ("paragraph", "text")),
            ("chunk-code-logical", "code", ("code", "code")),
        ):
            result = CliRunner().invoke(
                main,
                ["--root", str(root), "read", "chunk", logical_id],
            )
            assert result.exit_code == 0, result.output
            read_locator = json.loads(result.output)["data"]["chunk"]["source_locator"]
            text_item = retrieval.search_text(query, limit=10).results[0]
            text_locator = text_item.source_locator.to_dict()
            assert read_locator == text_locator
            assert (read_locator["block_kind"], read_locator["chunk_kind"]) == expected_kind
            assert read_locator["chunk_logical_id"] == logical_id
            assert read_locator["block_logical_id"] == f"block-{expected_kind[1]}-logical"
            assert read_locator["relative_path"] == "legacy.md"
            assert read_locator["start_offset"] is None
            assert read_locator["end_offset"] is None
    finally:
        conn.close()


def test_compiled_migration_manifest_reproduces_exact_identity() -> None:
    entries = [(path.name, path.read_bytes()) for path in sorted(MIGRATIONS_DIR.glob("*.sql"))]

    assert EXPECTED_MIGRATION_VERSION == "0006"
    assert [(name, hashlib.sha256(content).hexdigest()) for name, content in entries] == list(
        EXPECTED_MIGRATION_MANIFEST
    )
    assert _framed_manifest_digest(entries) == EXPECTED_MIGRATION_MANIFEST_DIGEST
    assert EXPECTED_MIGRATION_MANIFEST_DIGEST == "bd33d44185be1edb9bca9c2d82eed3b013f5ba8425b3d8ad98f0cf69c1e6a700"


def test_validated_pending_migration_failure_rolls_back_schema_and_ledger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migrations_dir = tmp_path / "broken"
    migrations_dir.mkdir()
    path = migrations_dir / "0000_broken.sql"
    path.write_text("CREATE TABLE should_rollback (id INTEGER);\nTHIS IS NOT SQL;\n", encoding="utf-8")
    content = path.read_bytes()
    monkeypatch.setattr(migration_module, "EXPECTED_MIGRATION_VERSION", "0000")
    monkeypatch.setattr(
        migration_module,
        "EXPECTED_MIGRATION_MANIFEST",
        ((path.name, hashlib.sha256(content).hexdigest()),),
    )
    monkeypatch.setattr(
        migration_module,
        "EXPECTED_MIGRATION_MANIFEST_DIGEST",
        _framed_manifest_digest([(path.name, content)]),
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


@pytest.mark.parametrize("damage", ["missing", "extra", "renamed", "duplicate", "reordered", "tampered"])
def test_invalid_package_identity_fails_before_any_sql(tmp_path: Path, damage: str) -> None:
    migrations_dir = tmp_path / "migrations"
    shutil.copytree(MIGRATIONS_DIR, migrations_dir)
    if damage == "missing":
        (migrations_dir / "0003_provenance.sql").unlink()
    elif damage == "extra":
        shutil.copy(migrations_dir / "0006_complete_provenance.sql", migrations_dir / "0007_extra.sql")
    elif damage == "renamed":
        (migrations_dir / "0003_provenance.sql").rename(migrations_dir / "0003_renamed.sql")
    elif damage == "duplicate":
        shutil.copy(migrations_dir / "0003_provenance.sql", migrations_dir / "0003_duplicate.sql")
    elif damage == "reordered":
        first = migrations_dir / "0002_fts.sql"
        second = migrations_dir / "0003_provenance.sql"
        first_bytes, second_bytes = first.read_bytes(), second.read_bytes()
        first.write_bytes(second_bytes)
        second.write_bytes(first_bytes)
    else:
        with (migrations_dir / "0004_embedding_profiles.sql").open("ab") as stream:
            stream.write(b"-- tampered\n")

    class NoSqlConnection:
        def __getattr__(self, name: str) -> object:
            raise AssertionError(f"connection touched before package validation: {name}")

    with pytest.raises(MigrationPlanError):
        apply_migrations(NoSqlConnection(), migrations_dir)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "versions",
    [
        ["0000", "0002"],
        ["0000", "0000"],
        ["9999"],
        ["future"],
    ],
)
def test_invalid_ledger_fails_before_pending_sql(versions: list[str]) -> None:
    class Cursor:
        def fetchall(self) -> list[dict[str, str]]:
            return [{"version": version} for version in versions]

    class LedgerSpy:
        executescript_calls = 0

        def execute(self, sql: str) -> Cursor | None:
            if sql.startswith("SELECT"):
                return Cursor()
            return None

        def commit(self) -> None:
            return None

        def executescript(self, sql: str) -> None:
            self.executescript_calls += 1

    connection = LedgerSpy()
    with pytest.raises(MigrationPlanError):
        apply_migrations(connection, MIGRATIONS_DIR)  # type: ignore[arg-type]
    assert connection.executescript_calls == 0


def test_database_with_unknown_future_version_fails_closed() -> None:
    conn, db_path = _fresh_db()
    try:
        apply_migrations(conn, MIGRATIONS_DIR)
        conn.execute("INSERT INTO schema_migrations (version) VALUES ('9999')")
        conn.commit()
        with pytest.raises(MigrationPlanError, match="unknown or non-contiguous"):
            apply_migrations(conn, MIGRATIONS_DIR)
    finally:
        conn.close()
        db_path.unlink(missing_ok=True)
