"""Tests for doctor CLI behavior."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from mdrack.adapters.sqlite.generation_runtime import SQLiteGenerationRuntime
from mdrack.application.generation_manager import StoreGenerationManager
from mdrack.application.store_generations import (
    ActiveGenerationPointer,
    GenerationContractKind,
    GenerationRetention,
    GenerationState,
    RetentionMode,
    StoreGeneration,
)
from mdrack.cli import main
from mdrack.storage.sqlite.connection import get_connection
from mdrack.storage.sqlite.migrations import (
    EXPECTED_MIGRATION_MANIFEST_DIGEST,
    EXPECTED_MIGRATION_VERSION,
    apply_candidate_migrations,
    apply_migrations,
)

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
    assert set(payload["data"]["findings"][0]["details"]) == {"reason_code"}
    assert str(tmp_path) not in result.output


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
        "configured_model": MODEL_LARGE,
        "profile_model": MODEL_SMALL,
        "configured_dimensions": 12,
        "profile_dimensions": 8,
    }


def test_doctor_reads_ready_active_generation_instead_of_clean_store(tmp_path: Path) -> None:
    decoy_path = _setup_db(tmp_path)
    decoy = get_connection(decoy_path)
    try:
        decoy.execute(
            "INSERT INTO files (id, relative_path, source_hash, indexed_at) VALUES (?, ?, ?, ?)",
            ("decoy-file", "decoy.md", "decoy-hash", "2026-07-18T00:00:00Z"),
        )
        decoy.execute(
            "INSERT INTO chunks (id, file_id, content, content_type, chunk_index) VALUES (?, ?, ?, ?, ?)",
            ("decoy-chunk", "decoy-file", "decoy", "text", 0),
        )
        decoy.commit()
    finally:
        decoy.close()

    manager = StoreGenerationManager(tmp_path / ".mdrack", runtime=SQLiteGenerationRuntime())
    generation_id = "active-doctor"
    active_path = manager.database_path(generation_id)
    active_path.parent.mkdir(parents=True, exist_ok=True)
    active = get_connection(active_path)
    try:
        apply_candidate_migrations(active, MIGRATIONS_DIR)
    finally:
        active.close()
    generation = StoreGeneration(
        generation_id=generation_id,
        contract_kind=GenerationContractKind.RESOURCE_CORE_V1,
        migration_manifest_digest=EXPECTED_MIGRATION_MANIFEST_DIGEST,
        schema_version=EXPECTED_MIGRATION_VERSION,
        state=GenerationState.READY,
        created_at="2026-07-18T00:00:00Z",
        verified_at="2026-07-18T00:00:01Z",
    )
    manager.metadata_path(generation_id).write_bytes(generation.to_bytes())
    manager.pointer_path.write_bytes(
        ActiveGenerationPointer(
            generation_id,
            GenerationContractKind.RESOURCE_CORE_V1,
        ).to_bytes()
    )
    active_bytes = active_path.read_bytes()

    result = CliRunner().invoke(main, ["--root", str(tmp_path), "doctor"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)["data"]
    findings = {finding["code"]: finding for finding in payload["findings"]}
    assert "GENERATION_READY" in findings
    assert "SCHEMA_LATEST" in findings
    assert "MISSING_FTS" not in findings
    assert active_path.read_bytes() == active_bytes
    assert generation_id not in result.output
    assert str(tmp_path) not in result.output


def test_doctor_reports_corrupt_generation_pointer_with_safe_fixed_fields(tmp_path: Path) -> None:
    _setup_db(tmp_path)
    pointer = tmp_path / ".mdrack" / "active-generation.json"
    pointer.write_text("PRIVATE_PATH_VECTOR_EXCEPTION_SENTINEL", encoding="utf-8")

    result = CliRunner().invoke(main, ["--root", str(tmp_path), "doctor"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    findings = {finding["code"]: finding for finding in payload["data"]["findings"]}
    assert findings["GENERATION_POINTER_INVALID"] == {
        "severity": "error",
        "code": "GENERATION_POINTER_INVALID",
        "message": "The active store generation pointer is invalid",
        "details": {"generation_state": "failed", "reason_code": "pointer_invalid"},
    }
    assert "PRIVATE_PATH_VECTOR_EXCEPTION_SENTINEL" not in result.output
    assert str(tmp_path) not in result.output


@pytest.mark.parametrize(
    "state",
    [
        GenerationState.LEGACY_ONLY,
        GenerationState.BUILDING,
        GenerationState.READY,
        GenerationState.FAILED,
    ],
)
def test_doctor_reports_missing_pointer_with_any_generation_metadata_as_error(
    tmp_path: Path,
    state: GenerationState,
) -> None:
    _setup_db(tmp_path)
    manager = StoreGenerationManager(tmp_path / ".mdrack", runtime=SQLiteGenerationRuntime())
    manager.generations_dir.mkdir(parents=True, exist_ok=True)
    legacy = state is GenerationState.LEGACY_ONLY
    generation = StoreGeneration(
        generation_id=f"private-{state.value}",
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

    result = CliRunner().invoke(main, ["--root", str(tmp_path), "doctor"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    findings = {finding["code"]: finding for finding in payload["data"]["findings"]}
    assert findings["GENERATION_POINTER_MISSING"] == {
        "severity": "error",
        "code": "GENERATION_POINTER_MISSING",
        "message": "The active store generation pointer is missing",
        "details": {"generation_state": "failed", "reason_code": "pointer_missing"},
    }
    assert payload["data"]["ok"] is False
    assert str(tmp_path) not in result.output
    assert generation.generation_id not in result.output
