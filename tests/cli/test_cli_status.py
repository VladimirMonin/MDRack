"""Tests for the status CLI command."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from mdrack.adapters.sqlite.generation_runtime import SQLiteGenerationRuntime
from mdrack.application.generation_manager import StoreGenerationManager
from mdrack.application.store_generations import (
    GenerationContractKind,
    GenerationRetention,
    GenerationState,
    RetentionMode,
    StoreGeneration,
)
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


def test_status_fails_closed_for_incomplete_generation_without_pointer_or_private_paths(
    tmp_path: Path,
) -> None:
    _setup_db(tmp_path)
    manager = StoreGenerationManager(
        tmp_path / ".mdrack",
        runtime=SQLiteGenerationRuntime(),
    )
    manager.generations_dir.mkdir(parents=True, exist_ok=True)
    generation = StoreGeneration(
        generation_id="candidate-1",
        contract_kind=GenerationContractKind.RESOURCE_CORE_V1,
        migration_manifest_digest="a" * 64,
        schema_version="0007",
        state=GenerationState.BUILDING,
        created_at="2026-07-18T00:00:00Z",
    )
    manager.metadata_path(generation.generation_id).write_bytes(generation.to_bytes())

    result = CliRunner().invoke(main, ["--root", str(tmp_path), "status"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)["data"]
    assert data["generation_state"] == "failed"
    assert "generation_building_count" not in data
    assert str(tmp_path) not in result.output
    assert "candidate-1" not in result.output


@pytest.mark.parametrize(
    "state",
    [
        GenerationState.LEGACY_ONLY,
        GenerationState.BUILDING,
        GenerationState.READY,
        GenerationState.FAILED,
    ],
)
def test_status_missing_pointer_fails_closed_when_generation_metadata_exists(
    tmp_path: Path,
    state: GenerationState,
) -> None:
    _setup_db(tmp_path)
    manager = StoreGenerationManager(tmp_path / ".mdrack", runtime=SQLiteGenerationRuntime())
    manager.generations_dir.mkdir(parents=True, exist_ok=True)
    legacy = state is GenerationState.LEGACY_ONLY
    generation = StoreGeneration(
        generation_id=f"missing-{state.value}",
        contract_kind=(
            GenerationContractKind.LEGACY_V0_2
            if legacy
            else GenerationContractKind.RESOURCE_CORE_V1
        ),
        migration_manifest_digest="a" * 64,
        schema_version="0006" if legacy else "0007",
        state=state,
        created_at="2026-07-18T00:00:00Z",
        verified_at="2026-07-18T00:00:00Z" if state is GenerationState.READY else None,
        failure_reason_code="rebuild_failed" if state is GenerationState.FAILED else None,
        retention=(
            GenerationRetention(RetentionMode.RETAINED_READ_ONLY, "v0.3")
            if legacy
            else GenerationRetention()
        ),
    )
    manager.metadata_path(generation.generation_id).write_bytes(generation.to_bytes())

    result = CliRunner().invoke(main, ["--root", str(tmp_path), "status"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["data"]["generation_state"] == "failed"
    assert str(tmp_path) not in result.output
    assert generation.generation_id not in result.output
