"""Tests for CLI text search via FTS5."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from click.testing import CliRunner

from mdrack.cli import main
from mdrack.storage.sqlite.connection import get_connection
from mdrack.storage.sqlite.migrations import apply_migrations

_MIGRATIONS_DIR = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "mdrack"
    / "storage"
    / "sqlite"
    / "migrations"
)


def _setup_db(tmp_path: Path, with_data: bool = True) -> Path:
    store_dir = tmp_path / ".mdrack"
    store_dir.mkdir()
    db_path = store_dir / "knowledge.db"
    conn = get_connection(db_path)
    try:
        apply_migrations(conn, _MIGRATIONS_DIR)
        if with_data:
            _seed_text_search_data(conn)
    finally:
        conn.close()
    return db_path


def _seed_text_search_data(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO files (id, relative_path, source_hash, indexed_at) "
        "VALUES (?, ?, ?, ?)",
        ("file-001", "docs/python.md", "hash-aaa", "2024-01-01T00:00:00Z"),
    )
    conn.execute(
        "INSERT INTO sections (id, file_id, title, level, start_line, end_line) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("section-001", "file-001", "Python Programming", 1, 1, 50),
    )
    conn.execute(
        "INSERT INTO chunks (id, file_id, section_id, content, content_type, chunk_index) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            "chunk-001",
            "file-001",
            "section-001",
            "Python is a high-level programming language.",
            "text",
            0,
        ),
    )
    conn.execute(
        "INSERT INTO chunks_fts (chunk_id, content, content_type, heading_path) "
        "VALUES (?, ?, ?, ?)",
        ("chunk-001", "Python is a high-level programming language.", "text", "Python"),
    )
    conn.execute(
        "INSERT INTO chunks (id, file_id, content, content_type, chunk_index) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            "chunk-002",
            "file-001",
            "JavaScript is a scripting language for the web.",
            "text",
            1,
        ),
    )
    conn.execute(
        "INSERT INTO chunks_fts (chunk_id, content, content_type, heading_path) "
        "VALUES (?, ?, ?, ?)",
        (
            "chunk-002",
            "JavaScript is a scripting language for the web.",
            "text",
            "",
        ),
    )
    conn.commit()


def test_text_search_returns_valid_json(tmp_path: Path) -> None:
    _setup_db(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--root", str(tmp_path), "search", "Python", "--mode", "text"],
    )
    assert result.exit_code == 0, f"search failed: {result.output}"
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert "data" in payload
    assert "meta" in payload
    assert payload["data"]["mode"] == "text"
    assert len(payload["data"]["results"]) > 0


def test_text_search_with_no_results(tmp_path: Path) -> None:
    _setup_db(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--root", str(tmp_path), "search", "NonexistentTermXYZ", "--mode", "text"],
    )
    assert result.exit_code == 0, f"search failed: {result.output}"
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert len(payload["data"]["results"]) == 0
    assert payload["data"]["total_count"] == 0


def test_text_search_output_format(tmp_path: Path) -> None:
    _setup_db(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--root", str(tmp_path), "search", "Python", "--mode", "text"],
    )
    assert result.exit_code == 0, f"search failed: {result.output}"
    payload = json.loads(result.output)
    assert payload["ok"] is True
    data = payload["data"]
    assert "query" in data
    assert data["query"] == "Python"
    assert "mode" in data
    assert "results" in data
    assert "total_count" in data
    for item in data["results"]:
        assert "chunk_id" in item
        assert "score" in item
        assert "snippet" in item
        assert "file" in item


def test_text_search_envelope_shape(tmp_path: Path) -> None:
    _setup_db(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--root", str(tmp_path), "search", "Python", "--mode", "text"],
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert "ok" in payload
    assert "data" in payload
    assert "meta" in payload
    assert "command" in payload["meta"]
    assert payload["ok"] is True


def test_text_search_no_db(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--root", str(tmp_path), "search", "Python", "--mode", "text"],
    )
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert "not found" in payload["error"]["message"].lower()
