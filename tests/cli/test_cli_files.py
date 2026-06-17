"""Tests for CLI files commands."""

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


def test_files_list_returns_valid_json(tmp_path: Path) -> None:
    """`mdrack files list` should return a valid JSON envelope."""
    db_path = _setup_db(tmp_path)

    # Insert sample files
    conn = get_connection(db_path)
    try:
        _insert_sample_file(conn, "file-1", "docs/readme.md")
        _insert_sample_file(conn, "file-2", "docs/guide.md")
        _insert_sample_file(conn, "file-3", "tutorial.md")
    finally:
        conn.close()

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--root", str(tmp_path), "files", "list"],
    )
    assert result.exit_code == 0, f"files list failed: {result.output}"
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert "data" in payload
    assert "files" in payload["data"]
    assert "pagination" in payload["data"]


def test_files_list_with_pagination(tmp_path: Path) -> None:
    """`mdrack files list` should support pagination."""
    db_path = _setup_db(tmp_path)

    # Insert 5 sample files
    conn = get_connection(db_path)
    try:
        for i in range(1, 6):
            _insert_sample_file(conn, f"file-{i}", f"doc{i}.md")
    finally:
        conn.close()

    runner = CliRunner()

    # Page 0 with page-size 2
    result = runner.invoke(
        main,
        ["--root", str(tmp_path), "files", "list", "--page", "0", "--page-size", "2"],
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["ok"] is True
    files = payload["data"]["files"]
    assert len(files) == 2
    assert payload["data"]["pagination"]["page"] == 0
    assert payload["data"]["pagination"]["page_size"] == 2
    assert payload["data"]["pagination"]["total"] == 5
    assert payload["data"]["pagination"]["has_next"] is True

    # Page 2 with page-size 2
    result = runner.invoke(
        main,
        ["--root", str(tmp_path), "files", "list", "--page", "2", "--page-size", "2"],
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert len(payload["data"]["files"]) == 1
    assert payload["data"]["pagination"]["has_next"] is False


def test_files_list_invalid_pagination(tmp_path: Path) -> None:
    """`mdrack files list` should reject invalid pagination parameters."""
    _setup_db(tmp_path)

    runner = CliRunner()

    # Negative page
    result = runner.invoke(
        main,
        ["--root", str(tmp_path), "files", "list", "--page", "-1"],
    )
    assert result.exit_code != 0
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "VALIDATION_ERROR"

    # Zero page size
    result = runner.invoke(
        main,
        ["--root", str(tmp_path), "files", "list", "--page-size", "0"],
    )
    assert result.exit_code != 0
    payload = json.loads(result.output)
    assert payload["ok"] is False


def test_files_info_returns_valid_json(tmp_path: Path) -> None:
    """`mdrack files info <file_id>` should return file metadata."""
    db_path = _setup_db(tmp_path)

    conn = get_connection(db_path)
    try:
        _insert_sample_file(conn, "file-xyz", "docs/test.md")
    finally:
        conn.close()

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--root", str(tmp_path), "files", "info", "file-xyz"],
    )
    assert result.exit_code == 0, f"files info failed: {result.output}"
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert "data" in payload
    assert "file" in payload["data"]
    file_data = payload["data"]["file"]
    assert file_data["id"] == "file-xyz"
    assert file_data["relative_path"] == "docs/test.md"


def test_files_info_not_found(tmp_path: Path) -> None:
    """`mdrack files info` with non-existent file should return error."""
    _setup_db(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--root", str(tmp_path), "files", "info", "non-existent-id"],
    )
    # Should succeed (exit 0) but return error envelope in JSON
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "NOT_FOUND"
    assert "not found" in payload["error"]["message"].lower()


def test_files_commands_use_root_relative_store_from_ctx(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    _write_config(root, ".custom-store")

    db_path = _setup_db_at_store(root, ".custom-store")
    conn = get_connection(db_path)
    try:
        _insert_sample_file(conn, "file-ctx", "docs/from-config.md")
    finally:
        conn.close()

    external_cwd = tmp_path / "outside"
    external_cwd.mkdir()
    monkeypatch.chdir(external_cwd)

    runner = CliRunner()
    result = runner.invoke(main, ["--root", str(root), "files", "info", "file-ctx"])

    assert result.exit_code == 0, f"files info failed: {result.output}"
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["data"]["file"]["id"] == "file-ctx"
