"""Tests for the scan CLI command."""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from mdrack.cli import main
from mdrack.indexing.indexer import IndexerResult
from mdrack.indexing.indexer import run_indexer as actual_run_indexer


def _write_config(root: Path, relative_path: str, store: str) -> Path:
    config_path = root / relative_path
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        f"[paths]\nstore = \"{store}\"\n",
        encoding="utf-8",
    )
    return config_path


def test_scan_returns_valid_json(tmp_path: Path) -> None:
    """`mdrack scan` should return a valid JSON envelope."""
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "test.md").write_text("# Hello\n\nWorld\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--root", str(tmp_path), "scan", "--provider", "fake"],
    )
    assert result.exit_code == 0, f"scan failed: {result.output}"
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert "data" in payload
    assert "meta" in payload
    assert payload["data"]["files_seen"] == 1


def test_scan_with_provider_fake(tmp_path: Path) -> None:
    """`mdrack scan --provider fake` should complete successfully."""
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "test.md").write_text("# Foo\n\nBar\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--root", str(tmp_path), "scan", "--provider", "fake"],
    )
    assert result.exit_code == 0, f"scan failed: {result.output}"
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["data"]["files_seen"] == 1
    assert payload["data"]["chunks_created"] > 0


def test_scan_output_format(tmp_path: Path) -> None:
    """`mdrack scan` output should contain expected fields."""
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "test.md").write_text("# Hello\n\nWorld\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--root", str(tmp_path), "scan", "--provider", "fake"],
    )
    assert result.exit_code == 0, f"scan failed: {result.output}"
    payload = json.loads(result.output)
    assert payload["ok"] is True
    data = payload["data"]
    assert isinstance(data["run_id"], str)
    assert isinstance(data["files_seen"], int)
    assert isinstance(data["files_changed"], int)
    assert isinstance(data["files_deleted"], int)
    assert isinstance(data["chunks_created"], int)
    assert payload["meta"]["command"] == "scan"


def test_scan_on_empty_directory(tmp_path: Path) -> None:
    """`mdrack scan` on an empty directory should return zero seen/changed/chunks counts."""
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--root", str(tmp_path), "scan", "--provider", "fake"],
    )
    assert result.exit_code == 0, f"scan failed: {result.output}"
    payload = json.loads(result.output)
    assert payload["ok"] is True
    data = payload["data"]
    assert data["files_seen"] == 0
    assert data["files_changed"] == 0
    assert data["chunks_created"] == 0
    assert isinstance(data["files_deleted"], int)


def test_scan_uses_root_relative_default_config_from_external_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "docs").mkdir()
    (root / "docs" / "test.md").write_text("# Hello\n\nWorld\n", encoding="utf-8")
    _write_config(root, ".mdrack/config.toml", ".custom-store")

    external_cwd = tmp_path / "outside"
    external_cwd.mkdir()
    monkeypatch.chdir(external_cwd)

    runner = CliRunner()
    result = runner.invoke(main, ["--root", str(root), "scan", "--provider", "fake"])

    assert result.exit_code == 0, f"scan failed: {result.output}"
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert (root / ".custom-store" / "knowledge.db").is_file()
    assert not (external_cwd / ".mdrack" / "knowledge.db").exists()


def test_scan_uses_root_relative_config_file_argument_from_external_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "docs").mkdir()
    (root / "docs" / "test.md").write_text("# Hello\n\nWorld\n", encoding="utf-8")
    _write_config(root, "configs/custom.toml", ".explicit-store")

    external_cwd = tmp_path / "outside"
    external_cwd.mkdir()
    monkeypatch.chdir(external_cwd)

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--root", str(root), "--config-file", "configs/custom.toml", "scan", "--provider", "fake"],
    )

    assert result.exit_code == 0, f"scan failed: {result.output}"
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert (root / ".explicit-store" / "knowledge.db").is_file()


def test_scan_defaults_to_configured_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "test.md").write_text("# Hello\n\nWorld\n", encoding="utf-8")

    captured: dict[str, object] = {}

    def fake_create_provider(provider_name: str, config) -> object:
        captured["provider_name"] = provider_name
        captured["config_model"] = config.embedding.model
        return MagicMock()

    def fake_run_indexer(root: Path, config, provider=None) -> IndexerResult:
        captured["provider"] = provider
        return IndexerResult(
            run_id="run-123",
            files_seen=1,
            files_changed=1,
            files_deleted=0,
            chunks_created=1,
        )

    monkeypatch.setattr("mdrack.cli.commands.scan._create_provider", fake_create_provider)
    monkeypatch.setattr("mdrack.cli.commands.scan.run_indexer", fake_run_indexer)

    runner = CliRunner()
    result = runner.invoke(main, ["--root", str(tmp_path), "scan"])

    assert result.exit_code == 0, f"scan failed: {result.output}"
    assert captured["provider_name"] == "lmstudio"
    assert captured["config_model"] == "qwen3-embedding-0.6b"
    assert captured["provider"] is not None


@pytest.mark.parametrize(
    ("status", "exit_code"),
    [("partial_success", 0), ("failed", 1)],
)
def test_scan_additive_status_fields_and_all_failed_exit_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    exit_code: int,
) -> None:
    def fake_run_indexer(root: Path, config, provider=None) -> IndexerResult:
        return IndexerResult(
            run_id="run-safe",
            status=status,
            files_seen=2,
            files_changed=2,
            files_indexed=1 if status == "partial_success" else 0,
            files_failed=1 if status == "partial_success" else 2,
            files_deleted=0,
            chunks_created=1 if status == "partial_success" else 0,
            errors_count=1 if status == "partial_success" else 2,
        )

    monkeypatch.setattr("mdrack.cli.commands.scan.run_indexer", fake_run_indexer)
    result = CliRunner().invoke(main, ["--root", str(tmp_path), "scan", "--provider", "fake"])

    assert result.exit_code == exit_code
    payload = json.loads(result.output)
    assert payload["ok"] is True
    legacy_keys = {"run_id", "files_seen", "files_changed", "files_deleted", "chunks_created"}
    assert legacy_keys <= set(payload["data"])
    assert payload["data"]["status"] == status
    assert payload["data"]["files_indexed"] == (1 if status == "partial_success" else 0)
    assert payload["data"]["files_failed"] == (1 if status == "partial_success" else 2)
    assert payload["data"]["errors_count"] == (1 if status == "partial_success" else 2)


def test_scan_internal_error_does_not_expose_private_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_path = tmp_path / "private-customer-note.md"

    def fail_run_indexer(root: Path, config, provider=None) -> IndexerResult:
        raise OSError(f"cannot read {private_path}")

    monkeypatch.setattr("mdrack.cli.commands.scan.run_indexer", fail_run_indexer)
    result = CliRunner().invoke(main, ["--root", str(tmp_path), "scan", "--provider", "fake"])

    assert result.exit_code == 1
    assert str(private_path) not in result.output
    payload = json.loads(result.output)
    assert payload["error"]["code"] == "INTERNAL_ERROR"
    assert payload["error"]["message"] == "Scan failed"


@pytest.mark.parametrize(
    "failure",
    [PermissionError("private permission detail"), OSError("private traversal detail")],
    ids=["inaccessible", "traversal-failed"],
)
def test_scan_traversal_failure_returns_error_envelope_and_preserves_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: OSError,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "note.md").write_text("# Kept\n\nLast good content.\n", encoding="utf-8")
    store = tmp_path / "store"
    config_path = _write_config(root, ".mdrack/config.toml", str(store))
    runner = CliRunner()
    first = runner.invoke(
        main,
        ["--root", str(root), "--config-file", str(config_path), "scan", "--provider", "fake"],
    )
    assert first.exit_code == 0

    def failing_walk(*args, **kwargs):
        kwargs["onerror"](failure)
        return iter(())

    monkeypatch.setattr(os, "walk", failing_walk)
    failed = runner.invoke(
        main,
        ["--root", str(root), "--config-file", str(config_path), "scan", "--provider", "fake"],
    )

    assert failed.exit_code == 1
    payload = json.loads(failed.output)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "CORPUS_SCAN_FAILED"
    assert str(failure) not in failed.output
    conn = sqlite3.connect(store / "knowledge.db")
    try:
        assert conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] > 0
    finally:
        conn.close()


def test_scan_missing_root_returns_error_envelope_and_preserves_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "note.md").write_text("# Kept\n\nLast good content.\n", encoding="utf-8")
    store = tmp_path / "store"
    config_path = _write_config(root, ".mdrack/config.toml", str(store))
    runner = CliRunner()
    args = ["--root", str(root), "--config-file", str(config_path), "scan", "--provider", "fake"]
    assert runner.invoke(main, args).exit_code == 0

    def remove_then_run(root: Path, config, provider=None):
        shutil.rmtree(root)
        return actual_run_indexer(root=root, config=config, provider=provider)

    monkeypatch.setattr("mdrack.cli.commands.scan.run_indexer", remove_then_run)
    failed = runner.invoke(main, args)

    assert failed.exit_code == 1
    payload = json.loads(failed.output)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "CORPUS_SCAN_FAILED"
    conn = sqlite3.connect(store / "knowledge.db")
    try:
        assert conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] > 0
    finally:
        conn.close()


def test_scan_config_debug_logs_do_not_expose_private_values(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    root = tmp_path / "private-customer-vault"
    root.mkdir()
    (root / "note.md").write_text("# Safe fixture\n", encoding="utf-8")
    endpoint = "https://private.example.test/v1?tenant=customer-secret"
    store = tmp_path / "private-customer-store"
    config_path = root / ".mdrack" / "private-config.toml"
    config_path.parent.mkdir()
    config_path.write_text(
        f'[paths]\nstore = "{store}"\n[embedding]\nendpoint = "{endpoint}"\n',
        encoding="utf-8",
    )
    caplog.set_level(logging.DEBUG, logger="mdrack.config.loader")

    result = CliRunner().invoke(
        main,
        ["--root", str(root), "--config-file", str(config_path), "scan", "--provider", "fake"],
    )

    assert result.exit_code == 0
    captured = caplog.text + result.output
    for private_value in (str(root), str(config_path), str(store), endpoint, "private-customer-vault"):
        assert private_value not in captured
