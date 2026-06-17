"""Tests for doctor CLI behavior."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from mdrack.cli import main
from mdrack.storage.sqlite.connection import get_connection
from mdrack.storage.sqlite.migrations import apply_migrations

MIGRATIONS_DIR = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "mdrack"
    / "storage"
    / "sqlite"
    / "migrations"
)


def _setup_db(tmp_path: Path) -> Path:
    store_dir = tmp_path / ".mdrack"
    store_dir.mkdir()
    db_path = store_dir / "knowledge.db"
    conn = get_connection(db_path)
    try:
        apply_migrations(conn, MIGRATIONS_DIR)
    finally:
        conn.close()
    return db_path


def test_doctor_reports_missing_database_as_structured_json(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["--root", str(tmp_path), "doctor"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["data"]["ok"] is False
    assert payload["data"]["summary"]["errors"] == 1
    assert payload["data"]["findings"][0]["code"] == "DATABASE_NOT_FOUND"


def test_doctor_reports_seeded_fts_inconsistency(tmp_path: Path) -> None:
    db_path = _setup_db(tmp_path)
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO files (id, relative_path, source_hash, indexed_at) VALUES (?, ?, ?, ?)",
            ("file-1", "docs/test.md", "hash-1", "2026-01-01T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO chunks (id, file_id, content, content_type, chunk_index) VALUES (?, ?, ?, ?, ?)",
            ("chunk-1", "file-1", "Missing FTS entry", "text", 0),
        )
        conn.commit()
    finally:
        conn.close()

    runner = CliRunner()
    result = runner.invoke(main, ["--root", str(tmp_path), "doctor"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["data"]["ok"] is False
    assert payload["data"]["summary"]["errors"] >= 1
    assert any(
        finding["code"] == "MISSING_FTS"
        for finding in payload["data"]["findings"]
    )
