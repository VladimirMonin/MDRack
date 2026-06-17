"""Tests for CLI sections commands."""

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
    db_path = store_dir / "knowledge.db"
    conn = get_connection(db_path)
    try:
        apply_migrations(conn, _MIGRATIONS_DIR)
    finally:
        conn.close()
    return db_path


def _setup_db_at_store(root: Path, store_name: str) -> Path:
    store_dir = root / store_name
    store_dir.mkdir()
    db_path = store_dir / "knowledge.db"
    conn = get_connection(db_path)
    try:
        apply_migrations(conn, _MIGRATIONS_DIR)
    finally:
        conn.close()
    return db_path


def _write_config(root: Path, store: str) -> None:
    config_dir = root / ".mdrack"
    config_dir.mkdir(exist_ok=True)
    (config_dir / "config.toml").write_text(
        f"[paths]\nstore = \"{store}\"\n",
        encoding="utf-8",
    )


def _make_config(tmp_path: Path) -> MDRackConfig:
    """Return a config rooted at tmp_path."""
    return load_config(
        cli_overrides={
            "paths.root": str(tmp_path),
            "paths.store": ".mdrack",
        }
    )


def _insert_sample_file(conn: sqlite3.Connection, file_id: str, path: str) -> None:
    """Insert a sample file record."""
    conn.execute(
        "INSERT INTO files (id, relative_path, source_hash, indexed_at) VALUES (?, ?, ?, ?)",
        (file_id, path, "hash123", "2024-01-01T00:00:00Z"),
    )
    conn.commit()


def _insert_sample_section(
    conn: sqlite3.Connection, section_id: str, file_id: str, title: str, start: int, end: int
) -> None:
    """Insert a sample section record."""
    conn.execute(
        "INSERT INTO sections (id, file_id, title, start_line, end_line, level) VALUES (?, ?, ?, ?, ?, ?)",
        (section_id, file_id, title, start, end, 1),
    )
    conn.commit()


def test_sections_list_returns_valid_json(tmp_path: Path) -> None:
    """`mdrack sections <file_id>` should return a valid JSON envelope."""
    db_path = _setup_db(tmp_path)

    conn = get_connection(db_path)
    try:
        _insert_sample_file(conn, "file-1", "docs/readme.md")
        _insert_sample_section(conn, "section-1", "file-1", "Introduction", 1, 10)
        _insert_sample_section(conn, "section-2", "file-1", "Usage", 11, 25)
    finally:
        conn.close()

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--root", str(tmp_path), "sections", "list", "file-1"],
    )
    assert result.exit_code == 0, f"sections list failed: {result.output}"
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert "data" in payload
    assert "sections" in payload["data"]
    sections = payload["data"]["sections"]
    assert len(sections) == 2
    assert sections[0]["title"] == "Introduction"
    assert sections[1]["title"] == "Usage"


def test_sections_list_for_nonexistent_file(tmp_path: Path) -> None:
    """`mdrack sections list` with non-existent file should return error."""
    _setup_db(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--root", str(tmp_path), "sections", "list", "non-existent-file-id"],
    )
    # Should succeed (exit 0) but return error envelope in JSON
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "NOT_FOUND"


def test_sections_list_empty(tmp_path: Path) -> None:
    """`mdrack sections list` for file with no sections should return empty list."""
    db_path = _setup_db(tmp_path)

    conn = get_connection(db_path)
    try:
        _insert_sample_file(conn, "file-empty", "docs/empty.md")
    finally:
        conn.close()

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--root", str(tmp_path), "sections", "list", "file-empty"],
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["data"]["sections"] == []


def test_sections_use_root_relative_store_from_ctx(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    _write_config(root, ".custom-store")

    db_path = _setup_db_at_store(root, ".custom-store")
    conn = get_connection(db_path)
    try:
        _insert_sample_file(conn, "file-1", "docs/readme.md")
        _insert_sample_section(conn, "section-1", "file-1", "Introduction", 1, 10)
    finally:
        conn.close()

    external_cwd = tmp_path / "outside"
    external_cwd.mkdir()
    monkeypatch.chdir(external_cwd)

    runner = CliRunner()
    result = runner.invoke(main, ["--root", str(root), "sections", "list", "file-1"])

    assert result.exit_code == 0, f"sections list failed: {result.output}"
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["data"]["sections"][0]["id"] == "section-1"
