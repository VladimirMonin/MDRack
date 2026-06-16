"""Tests for the scan CLI command."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from mdrack.cli import main


def test_scan_returns_valid_json(tmp_path: Path) -> None:
    """`mdrack scan` should return a valid JSON envelope."""
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "test.md").write_text("# Hello\n\nWorld\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--root", str(tmp_path), "scan"],
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
        ["--root", str(tmp_path), "scan"],
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
        ["--root", str(tmp_path), "scan"],
    )
    assert result.exit_code == 0, f"scan failed: {result.output}"
    payload = json.loads(result.output)
    assert payload["ok"] is True
    data = payload["data"]
    assert data["files_seen"] == 0
    assert data["files_changed"] == 0
    assert data["chunks_created"] == 0
    assert isinstance(data["files_deleted"], int)
