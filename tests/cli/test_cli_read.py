"""Tests for the read CLI commands."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner

from mdrack.cli import main
from mdrack.config.loader import load_config
from mdrack.config.models import MDRackConfig
from mdrack.storage.sqlite.connection import get_connection
from mdrack.storage.sqlite.migrations import apply_migrations

_MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "src" / "mdrack" / "storage" / "sqlite" / "migrations"


def _setup_db(tmp_path: Path) -> Path:
    """Create a DB with schema migrations applied in tmp_path."""
    store_dir = tmp_path / ".mdrack"
    store_dir.mkdir()
    db_path = store_dir / "knowledge.db"  # Match CLI expectation
    conn = get_connection(db_path)
    try:
        apply_migrations(conn, _MIGRATIONS_DIR)
    finally:
        conn.close()
    return db_path


def _seed_data(conn: sqlite3.Connection) -> None:
    """Insert sample files, sections, chunks."""
    conn.execute(
        "INSERT INTO files (id, relative_path, title, source_hash, indexed_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("file1", "docs/test1.md", "Test1", "hash1", "2026-01-15T10:00:00"),
    )
    conn.execute(
        "INSERT INTO files (id, relative_path, title, source_hash, indexed_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("file2", "docs/test2.md", "Test2", "hash2", "2026-01-15T10:05:00"),
    )

    conn.execute(
        "INSERT INTO sections (id, file_id, title, level, start_line, end_line) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("section1", "file1", "Section 1", 1, 1, 10),
    )
    conn.execute(
        "INSERT INTO sections (id, file_id, title, level, start_line, end_line) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("section2", "file1", "Section 2", 2, 11, 20),
    )
    conn.execute(
        "INSERT INTO sections (id, file_id, title, level, start_line, end_line) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("section3", "file2", "Section A", 1, 1, 15),
    )

    conn.execute(
        "INSERT INTO chunks (id, file_id, section_id, content, content_type, chunk_index, "
        "heading_path, previous_chunk_id, next_chunk_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("chunk1", "file1", "section1", "Chunk 1 content", "text", 0, "Section 1", None, "chunk2"),
    )
    conn.execute(
        "INSERT INTO chunks (id, file_id, section_id, content, content_type, chunk_index, "
        "heading_path, previous_chunk_id, next_chunk_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("chunk2", "file1", "section1", "Chunk 2 content", "text", 1, "Section 1", "chunk1", "chunk3"),
    )
    conn.execute(
        "INSERT INTO chunks (id, file_id, section_id, content, content_type, chunk_index, "
        "heading_path, previous_chunk_id, next_chunk_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("chunk3", "file1", "section2", "Chunk 3 content", "text", 0, "Section 2", "chunk2", None),
    )
    conn.execute(
        "INSERT INTO chunks (id, file_id, section_id, content, content_type, chunk_index, "
        "heading_path, previous_chunk_id, next_chunk_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("chunk4", "file2", "section3", "Chunk 4 content", "text", 0, "Section A", None, None),
    )
    conn.commit()


def _make_config(tmp_path: Path) -> MDRackConfig:
    """Return a config rooted at tmp_path."""
    return load_config(
        cli_overrides={
            "paths.root": str(tmp_path),
            "paths.store": ".mdrack",
        }
    )


@pytest.fixture()
def seeded_db(tmp_path: Path) -> sqlite3.Connection:
    """Create a seeded database for testing."""
    db_path = _setup_db(tmp_path)
    conn = get_connection(db_path)
    _seed_data(conn)
    yield conn
    conn.close()


def test_read_chunk_returns_valid_json(seeded_db: sqlite3.Connection, tmp_path: Path) -> None:
    """`mdrack read chunk <chunk_id>` should return a valid JSON envelope."""
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--root", str(tmp_path), "read", "chunk", "chunk1"],
    )
    assert result.exit_code == 0, f"chunk read failed: {result.output}"
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert "data" in payload
    assert "meta" in payload
    assert "chunk" in payload["data"]
    assert payload["data"]["chunk"]["id"] == "chunk1"


def test_read_section_returns_valid_json(seeded_db: sqlite3.Connection, tmp_path: Path) -> None:
    """`mdrack read section <section_id>` should return a valid JSON envelope."""
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--root", str(tmp_path), "read", "section", "section1"],
    )
    assert result.exit_code == 0, f"section read failed: {result.output}"
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert "data" in payload
    assert "meta" in payload
    assert "section" in payload["data"]
    assert payload["data"]["section"]["id"] == "section1"
    assert "chunks" in payload["data"]
    assert len(payload["data"]["chunks"]) == 2  # chunk1 and chunk2


def test_read_file_returns_valid_json(seeded_db: sqlite3.Connection, tmp_path: Path) -> None:
    """`mdrack read file <file_id>` should return a valid JSON envelope."""
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--root", str(tmp_path), "read", "file", "file1"],
    )
    assert result.exit_code == 0, f"file read failed: {result.output}"
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert "data" in payload
    assert "meta" in payload
    assert "file" in payload["data"]
    assert payload["data"]["file"]["id"] == "file1"
    assert "sections" in payload["data"]
    assert len(payload["data"]["sections"]) == 2  # section1 and section2


def test_read_chunk_with_context_neighbors(seeded_db: sqlite3.Connection, tmp_path: Path) -> None:
    """`mdrack read chunk <chunk_id> --context neighbors` should return chunk + neighbors."""
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--root", str(tmp_path), "read", "chunk", "chunk2", "--context", "neighbors"],
    )
    assert result.exit_code == 0, f"chunk read with neighbors failed: {result.output}"
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert "data" in payload
    assert "neighbors" in payload["data"]
    neighbors = payload["data"]["neighbors"]
    assert len(neighbors) == 2  # previous (chunk1) and next (chunk3)
    assert neighbors[0]["id"] == "chunk1"
    assert neighbors[1]["id"] == "chunk3"


def test_read_chunk_with_context_neighbors_at_start(seeded_db: sqlite3.Connection, tmp_path: Path) -> None:
    """`mdrack read chunk` with --context neighbors at start should only have next neighbor."""
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--root", str(tmp_path), "read", "chunk", "chunk1", "--context", "neighbors"],
    )
    assert result.exit_code == 0, f"chunk read with neighbors failed: {result.output}"
    payload = json.loads(result.output)
    assert payload["ok"] is True
    neighbors = payload["data"].get("neighbors", [])
    assert len(neighbors) == 1  # only next chunk (chunk2), no previous
    assert neighbors[0]["id"] == "chunk2"


def test_read_chunk_with_context_neighbors_at_end(seeded_db: sqlite3.Connection, tmp_path: Path) -> None:
    """`mdrack read chunk` with --context neighbors at end should only have previous neighbor."""
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--root", str(tmp_path), "read", "chunk", "chunk3", "--context", "neighbors"],
    )
    assert result.exit_code == 0, f"chunk read with neighbors failed: {result.output}"
    payload = json.loads(result.output)
    assert payload["ok"] is True
    neighbors = payload["data"].get("neighbors", [])
    assert len(neighbors) == 1  # only previous chunk (chunk2)
    assert neighbors[0]["id"] == "chunk2"


def test_read_non_existent_chunk_returns_error(seeded_db: sqlite3.Connection, tmp_path: Path) -> None:
    """Reading a non-existent chunk should return an error envelope."""
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--root", str(tmp_path), "read", "chunk", "nonexistent"],
    )
    assert result.exit_code == 1, f"expected error but exit code was {result.exit_code}"
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert "error" in payload
    assert payload["error"]["code"] == "NOT_FOUND"


def test_read_non_existent_section_returns_error(seeded_db: sqlite3.Connection, tmp_path: Path) -> None:
    """Reading a non-existent section should return an error envelope."""
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--root", str(tmp_path), "read", "section", "nonexistent"],
    )
    assert result.exit_code == 1, f"expected error but exit code was {result.exit_code}"
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert "error" in payload
    assert payload["error"]["code"] == "NOT_FOUND"


def test_read_non_existent_file_returns_error(seeded_db: sqlite3.Connection, tmp_path: Path) -> None:
    """Reading a non-existent file should return an error envelope."""
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--root", str(tmp_path), "read", "file", "nonexistent"],
    )
    assert result.exit_code == 1, f"expected error but exit code was {result.exit_code}"
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert "error" in payload
    assert payload["error"]["code"] == "NOT_FOUND"
