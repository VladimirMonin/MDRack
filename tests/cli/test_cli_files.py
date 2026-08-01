"""Fixed-catalog tests for the ``mdrack files`` command group."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from click.testing import CliRunner

from mdrack.cli import main


def _invoke(runner: CliRunner, root: Path, *command: str) -> dict[str, Any]:
    result = runner.invoke(main, ["--root", str(root), *command])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    return payload["data"]


def _indexed_root(tmp_path: Path) -> tuple[Path, CliRunner]:
    root = tmp_path / "project"
    root.mkdir()
    (root / "zeta.md").write_text("# Zeta\n\nZeta document text.\n", encoding="utf-8")
    (root / "alpha.md").write_text("# Alpha\n\nAlpha document text.\n", encoding="utf-8")
    (root / "middle.md").write_text("# Middle\n\nMiddle document text.\n", encoding="utf-8")
    runner = CliRunner()
    _invoke(runner, root, "init")
    _invoke(runner, root, "scan", "--provider", "fake")
    return root, runner


def test_files_list_reads_document_resources_from_the_fixed_catalog(tmp_path: Path) -> None:
    root, runner = _indexed_root(tmp_path)

    data = _invoke(runner, root, "files", "list", "--page-size", "2")
    records = data["files"]
    pagination = data["pagination"]

    assert isinstance(records, list)
    assert [record["relative_path"] for record in records] == ["alpha.md", "middle.md"]
    assert pagination == {"page": 0, "page_size": 2, "total": 3, "has_next": True}

    next_page = _invoke(runner, root, "files", "list", "--page", "1", "--page-size", "2")
    assert [record["relative_path"] for record in next_page["files"]] == ["zeta.md"]
    assert next_page["pagination"]["has_next"] is False


def test_files_info_returns_the_same_public_record_as_files_list(tmp_path: Path) -> None:
    root, runner = _indexed_root(tmp_path)
    listed = _invoke(runner, root, "files", "list")
    records = listed["files"]
    assert isinstance(records, list) and records
    record = records[0]
    logical_id = record["logical_id"]

    info = _invoke(runner, root, "files", "info", str(logical_id))
    assert info["file"] == record
    assert info["file"]["id"] == logical_id


def test_files_reject_invalid_pagination_after_opening_the_fixed_catalog(tmp_path: Path) -> None:
    root, runner = _indexed_root(tmp_path)

    for arguments in (("--page", "-1"), ("--page-size", "0")):
        result = runner.invoke(main, ["--root", str(root), "files", "list", *arguments])
        assert result.exit_code == 1
        payload = json.loads(result.output)
        assert payload["error"]["code"] == "VALIDATION_ERROR"


def test_files_info_not_found_is_private_safe(tmp_path: Path) -> None:
    root, runner = _indexed_root(tmp_path)
    sentinel = "PRIVATE_FILE_ID_SENTINEL_/home/v/private.md"

    result = runner.invoke(main, ["--root", str(root), "files", "info", sentinel])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["error"] == {"message": "File not found", "code": "NOT_FOUND"}
    assert sentinel not in result.output


def test_files_uses_configured_store_relative_to_root(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "project"
    root.mkdir()
    config_dir = root / ".mdrack"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text('[paths]\nstore = ".custom-store"\n', encoding="utf-8")
    (root / "configured.md").write_text("# Configured\n\nConfigured store reader.\n", encoding="utf-8")
    runner = CliRunner()
    _invoke(runner, root, "init")
    _invoke(runner, root, "scan", "--provider", "fake")
    assert (root / ".custom-store" / "catalog.sqlite3").is_file()

    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(outside)
    data = _invoke(runner, root, "files", "list")
    assert [record["relative_path"] for record in data["files"]] == ["configured.md"]
