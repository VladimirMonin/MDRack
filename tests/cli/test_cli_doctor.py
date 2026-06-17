"""Tests for doctor CLI behavior."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from mdrack.cli import main
from mdrack.storage.sqlite.connection import get_connection
from mdrack.storage.sqlite.migrations import apply_migrations

MODEL_SMALL = "Qwen/Qwen3-Embedding-0.6B-GGUF"
MODEL_LARGE = "Qwen/Qwen3-Embedding-4B-GGUF"

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
    store_dir.mkdir(exist_ok=True)
    db_path = store_dir / "knowledge.db"
    conn = get_connection(db_path)
    try:
        apply_migrations(conn, MIGRATIONS_DIR)
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


def test_doctor_reports_profile_config_mismatch(tmp_path: Path) -> None:
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
    result = runner.invoke(main, ["--root", str(tmp_path), "doctor"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    findings = {finding["code"]: finding for finding in payload["data"]["findings"]}
    assert "PROFILE_CONFIG_MISMATCH" in findings
    assert findings["PROFILE_CONFIG_MISMATCH"]["details"] == {
        "profile": "default",
        "expected_model": MODEL_LARGE,
        "actual_model": MODEL_SMALL,
        "expected_dimensions": 12,
        "actual_dimensions": 8,
    }
