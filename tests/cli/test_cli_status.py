"""Tests for the status CLI command."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from mdrack.cli import main
from mdrack.storage.sqlite.connection import get_connection
from mdrack.storage.sqlite.migrations import apply_migrations

_MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "src" / "mdrack" / "storage" / "sqlite" / "migrations"
MODEL_SMALL = "Qwen/Qwen3-Embedding-0.6B-GGUF"
MODEL_LARGE = "Qwen/Qwen3-Embedding-4B-GGUF"


def _setup_db(tmp_path: Path) -> Path:
    """Create a DB with schema migrations applied in tmp_path."""
    store_dir = tmp_path / ".mdrack"
    store_dir.mkdir(exist_ok=True)
    db_path = store_dir / "knowledge.db"
    conn = get_connection(db_path)
    try:
        apply_migrations(conn, _MIGRATIONS_DIR)
    finally:
        conn.close()
    return db_path


def _write_config(root: Path, model_name: str, dimensions: int) -> Path:
    config_path = root / ".mdrack" / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        "\n".join(
            [
                "[paths]",
                'store = ".mdrack"',
                "",
                "[embedding]",
                'provider = "lmstudio"',
                f'model = "{model_name}"',
                'endpoint = "http://localhost:1234/v1"',
                f"dimensions = {dimensions}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return config_path


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


def test_status_reports_configured_and_profile_embedding_metadata(tmp_path: Path) -> None:
    _write_config(tmp_path, MODEL_LARGE, 12)
    db_path = _setup_db(tmp_path)
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO embedding_profiles (name, model, dimensions, endpoint) VALUES (?, ?, ?, ?)",
            ("default", MODEL_SMALL, 8, "http://localhost:1234/v1"),
        )
        conn.commit()
    finally:
        conn.close()

    runner = CliRunner()
    result = runner.invoke(main, ["--root", str(tmp_path), "status"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    data = payload["data"]
    assert data["active_profile"] == "default"
    assert data["configured_model"] == MODEL_LARGE
    assert data["configured_dimensions"] == 12
    assert data["configured_endpoint"] == "http://localhost:1234/v1"
    assert data["profile_model"] == MODEL_SMALL
    assert data["profile_dimensions"] == 8
    assert data["profile_endpoint"] == "http://localhost:1234/v1"
