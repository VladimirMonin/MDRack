"""Registered CLI selective refresh after deletion and change."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from mdrack.cli import main


def _folder_command(root: Path, folder: Path, *, excludes: tuple[str, ...] = ()) -> list[str]:
    command = [
        "--root", str(root), "folder", "add", f"fixture={folder}",
    ]
    for pattern in excludes:
        command += ["--exclude", pattern]
    command += ["--provider", "synthetic"]
    return command


def _scope_counts(root: Path) -> dict[str, int]:
    with sqlite3.connect(root / ".mdrack" / "catalog.sqlite3") as connection:
        return {
            str(table): int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in (
                "core_resources",
                "core_representations",
                "core_search_units",
                "core_search_units_fts",
                "core_unit_embeddings",
            )
        }


def _lifecycle(root: Path) -> dict[str, dict[str, dict[str, object]]]:
    raw = json.loads((root / ".mdrack" / "folder-lifecycle.json").read_text(encoding="utf-8"))
    return {
        str(group["namespace"]): {
            str(item["source_ref"]): item
            for item in group["files"]  # type: ignore[index]
        }
        for group in raw["roots"]
    }


def _make_junction(link: Path, target: Path) -> None:
    if os.name != "nt":
        pytest.skip("Windows junction policy control")
    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        timeout=15,
    )
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")


def _release_junction(link: Path | None) -> None:
    if link is None:
        return
    if link.exists() or link.is_junction():
        link.rmdir()


def _search_text(root: Path, query: str) -> list[dict[str, object]]:
    result = CliRunner().invoke(main, [
        "--root", str(root), "search", query, "--mode", "text",
    ])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)["data"]["results"]


def test_folder_add_reports_missing_and_refreshes_changed_files_from_cli(
    tmp_path: Path,
) -> None:
    folder = tmp_path / "SYNTHETIC_FIXTURE_selected"
    folder.mkdir()
    (folder / "keep.md").write_text("# Keep\n\nCLI_KEEP_OLD_TOKEN.\n", encoding="utf-8")
    (folder / "gone.md").write_text("# Gone\n\nCLI_GONE_TOKEN.\n", encoding="utf-8")
    app = tmp_path / "app"
    app.mkdir()
    runner = CliRunner()
    command = [
        "--root", str(app), "folder", "add", f"fixture={folder}",
        "--provider", "synthetic",
    ]

    first = runner.invoke(main, command)
    assert first.exit_code == 0, first.output
    assert json.loads(first.output)["data"]["counts"]["added"] == 2
    assert not (folder / "folder-lifecycle.json").exists()
    assert (app / ".mdrack" / "folder-lifecycle.json").is_file()

    (folder / "gone.md").unlink()
    second = runner.invoke(main, command)
    assert second.exit_code == 0, second.output
    data = json.loads(second.output)["data"]
    assert data["status"] == "success"
    assert data["counts"] == {
        "added": 0,
        "updated": 0,
        "skipped": 1,
        "failed": 0,
        "unprocessed": 0,
        "unavailable": 1,
        "excluded": 0,
        "total": 2,
    }
    assert data["roots"] == [{
        "namespace": "fixture",
        "files": 2,
        "added": 0,
        "updated": 0,
        "skipped": 1,
        "failed": 0,
        "unprocessed": 0,
        "unavailable": 1,
        "excluded": 0,
    }]
    by_ref = {str(item["source_ref"]): item for item in data["files"]}
    assert by_ref["gone.md"]["status"] == "unavailable"
    assert by_ref["gone.md"]["reason"] == "source_missing"
    assert by_ref["keep.md"]["status"] == "skipped"
    assert _search_text(app, "CLI_GONE_TOKEN") == []
    assert len(_search_text(app, "CLI_KEEP_OLD_TOKEN")) == 1
    assert str(folder) not in second.output

    (folder / "keep.md").write_text("# Keep\n\nCLI_KEEP_NEW_TOKEN.\n", encoding="utf-8")
    (folder / "new.txt").write_text("literal CLI_NEW_FILE_TOKEN\n", encoding="utf-8")
    third = runner.invoke(main, command)
    assert third.exit_code == 0, third.output
    refreshed = json.loads(third.output)["data"]
    assert refreshed["status"] == "success"
    assert refreshed["counts"]["updated"] == 1
    assert refreshed["counts"]["added"] == 1
    assert refreshed["counts"]["unavailable"] == 1
    assert refreshed["counts"]["skipped"] == 0
    assert _search_text(app, "CLI_KEEP_OLD_TOKEN") == []
    assert len(_search_text(app, "CLI_KEEP_NEW_TOKEN")) == 1
    assert len(_search_text(app, "CLI_NEW_FILE_TOKEN")) == 1
    assert _search_text(app, "CLI_GONE_TOKEN") == []


def test_cli_linked_subtree_is_not_missing_authority_and_restore_reuses_work(
    tmp_path: Path,
) -> None:
    selected = tmp_path / "SYNTHETIC_FIXTURE_selected"
    nested = selected / "nested"
    nested.mkdir(parents=True)
    original = nested / "inside.md"
    original.write_text("# Original\n\nCLI_UNSEEN_SCOPE_TOKEN.\n", encoding="utf-8")
    witness = hashlib.sha256(original.read_bytes()).hexdigest()
    app = tmp_path / "app"
    app.mkdir()
    runner = CliRunner()
    command = _folder_command(app, selected)

    first = runner.invoke(main, command)
    assert first.exit_code == 0, first.output
    first_data = json.loads(first.output)["data"]
    assert first_data["counts"]["added"] == 1
    before_counts = _scope_counts(app)
    before_lifecycle = _lifecycle(app)

    held = tmp_path / "held_originals"
    nested.rename(held)
    link: Path | None = selected / "nested"
    try:
        _make_junction(link, held)
        second = runner.invoke(main, command)
        assert second.exit_code == 1, second.output
        payload = json.loads(second.output)
        assert payload["ok"] is True
        data = payload["data"]
        assert data["status"] == "partial_success"
        assert data["counts"]["unavailable"] == 0
        by_ref = {str(item["source_ref"]): item for item in data["files"]}
        assert by_ref["nested"]["status"] == "skipped"
        assert by_ref["nested"]["reason"] == "link_not_followed"
        assert "nested/inside.md" not in by_ref
        assert _scope_counts(app) == before_counts
        assert _lifecycle(app) == before_lifecycle
        assert len(_search_text(app, "CLI_UNSEEN_SCOPE_TOKEN")) == 1
    finally:
        _release_junction(link)
        if held.exists():
            held.rename(nested)

    restored = runner.invoke(main, command)
    assert restored.exit_code == 0, restored.output
    restored_data = json.loads(restored.output)["data"]
    assert restored_data["status"] == "success"
    assert restored_data["counts"]["skipped"] == 1
    assert restored_data["counts"]["added"] == 0
    assert restored_data["counts"]["updated"] == 0
    assert restored_data["counts"]["unavailable"] == 0
    assert restored_data["model_calls"] == 0
    assert _scope_counts(app) == before_counts
    assert _lifecycle(app) == before_lifecycle
    assert len(_search_text(app, "CLI_UNSEEN_SCOPE_TOKEN")) == 1
    assert hashlib.sha256((selected / "nested" / "inside.md").read_bytes()).hexdigest() == witness


def test_cli_excluded_history_is_preserved_while_true_missing_reconciles(
    tmp_path: Path,
) -> None:
    selected = tmp_path / "SYNTHETIC_FIXTURE_selected"
    nested = selected / "nested"
    nested.mkdir(parents=True)
    inside = nested / "inside.md"
    inside.write_text("# Original\n\nCLI_EXCLUDED_SCOPE_TOKEN.\n", encoding="utf-8")
    witness = hashlib.sha256(inside.read_bytes()).hexdigest()
    gone = selected / "gone.md"
    gone.write_text("# Gone\n\nCLI_TRUE_MISSING_TOKEN.\n", encoding="utf-8")
    app = tmp_path / "app"
    app.mkdir()
    runner = CliRunner()
    command = _folder_command(app, selected)

    first = runner.invoke(main, command)
    assert first.exit_code == 0, first.output
    assert json.loads(first.output)["data"]["counts"]["added"] == 2
    before_nested = _lifecycle(app)["fixture"]["nested/inside.md"]

    held = tmp_path / "held_originals"
    held.mkdir()
    inside.rename(held / "inside.md")
    gone.unlink()
    excluded = _folder_command(app, selected, excludes=("nested/**",))
    refresh = runner.invoke(main, excluded)
    assert refresh.exit_code == 0, refresh.output
    data = json.loads(refresh.output)["data"]
    assert data["status"] == "success"
    assert data["counts"]["unavailable"] == 1
    assert data["counts"]["added"] == 0 and data["counts"]["updated"] == 0
    assert data["model_calls"] == 0
    by_ref = {str(item["source_ref"]): item for item in data["files"]}
    assert by_ref["gone.md"]["status"] == "unavailable"
    assert by_ref["gone.md"]["reason"] == "source_missing"
    assert "nested/inside.md" not in by_ref
    assert _scope_counts(app)["core_resources"] == 1
    state = _lifecycle(app)
    assert state["fixture"]["nested/inside.md"] == before_nested
    assert state["fixture"]["gone.md"]["availability"] == "unavailable"
    assert len(_search_text(app, "CLI_EXCLUDED_SCOPE_TOKEN")) == 1
    assert _search_text(app, "CLI_TRUE_MISSING_TOKEN") == []

    (held / "inside.md").rename(inside)
    restored = runner.invoke(main, command)
    assert restored.exit_code == 0, restored.output
    restored_data = json.loads(restored.output)["data"]
    assert restored_data["status"] == "success"
    assert restored_data["counts"]["skipped"] == 1
    assert restored_data["counts"]["unavailable"] == 1
    assert restored_data["model_calls"] == 0
    assert len(_search_text(app, "CLI_EXCLUDED_SCOPE_TOKEN")) == 1
    assert hashlib.sha256((selected / "nested" / "inside.md").read_bytes()).hexdigest() == witness
