"""Tests for the scan CLI command."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from mdrack.cli import main
from mdrack.indexing.indexer import IndexerResult


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
