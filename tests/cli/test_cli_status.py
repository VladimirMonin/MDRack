"""Tests for the status CLI command."""

from __future__ import annotations

import json
from pathlib import Path

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


def _make_config(tmp_path: Path) -> MDRackConfig:
    """Return a config rooted at tmp_path."""
    return load_config(
        cli_overrides={
            "paths.root": str(tmp_path),
            "paths.store": ".mdrack",
        }
    )


def test_status_returns_valid_json(tmp_path: Path) -> None:
    """`mdrack status` should return a valid JSON envelope."""
    _setup_db(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--root", str(tmp_path), "status"],
    )
    assert result.exit_code == 0, f"status failed: {result.output}"
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert "data" in payload
    assert "meta" in payload
    assert "status" in payload["meta"]["command"]


def test_status_shows_counts(tmp_path: Path) -> None:
    """Status on an empty store should report zero counts."""
    _setup_db(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--root", str(tmp_path), "status"],
    )
    assert result.exit_code == 0, f"status failed: {result.output}"
    payload = json.loads(result.output)
    data = payload["data"]
    assert data["files_count"] == 0
    assert data["chunks_count"] == 0
    assert data["embeddings_count"] == 0
    assert data["schema_version"] is not None


def test_status_on_empty_store(tmp_path: Path) -> None:
    """Status when no DB file exists should return zero counts and null schema."""
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--root", str(tmp_path), "status"],
    )
    assert result.exit_code == 0, f"status failed: {result.output}"
    payload = json.loads(result.output)
    data = payload["data"]
    assert data["files_count"] == 0
    assert data["chunks_count"] == 0
    assert data["embeddings_count"] == 0
    assert data["schema_version"] is None
