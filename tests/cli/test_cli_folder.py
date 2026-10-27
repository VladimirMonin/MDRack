"""Registered mixed-folder CLI behavior on disposable synthetic fixtures."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from click.testing import CliRunner

from mdrack.cli import main
from tests.integration.test_native_image_v2 import PNG


def test_folder_add_is_registered_and_routes_mixed_synthetic_sources(tmp_path: Path) -> None:
    folder = tmp_path / "selected"
    folder.mkdir()
    (folder / "notes.md").write_text("# Notes\n\nMixed folder marker.\n", encoding="utf-8")
    (folder / "plain.txt").write_text("Literal plain marker.\n", encoding="utf-8")
    (folder / "scene.png").write_bytes(PNG)

    result = CliRunner().invoke(main, [
        "--root", str(tmp_path), "folder", "add", f"fixture={folder}",
        "--provider", "synthetic",
    ])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    data = payload["data"]
    assert data["schema"] == "mdrack.folder-import.v1"
    assert data["counts"]["added"] == 3
    assert data["counts"]["failed"] == 0
    assert data["model_calls"] > 0

    text = CliRunner().invoke(main, [
        "--root", str(tmp_path), "search", "Mixed folder marker", "--mode", "text",
    ])
    assert text.exit_code == 0, text.output
    text_data = json.loads(text.output)["data"]
    assert text_data["results"][0]["source_locator"]["root_id"] == "fixture"
    assert text_data["results"][0]["source_locator"]["relative_path"] == "notes.md"

    image = CliRunner().invoke(main, [
        "--root", str(tmp_path), "image", "native-search", "--text", "scene",
        "--candidates", "image", "--provider", "synthetic", "--mode", "semantic",
    ])
    assert image.exit_code == 0, image.output
    image_data = json.loads(image.output)["data"]
    assert image_data["results"][0]["evidence"][0]["locator"]["payload"]["source_ref"] == "scene.png"


def test_folder_help_surface_is_registered_without_catalog_override() -> None:
    runner = CliRunner()
    group = runner.invoke(main, ["folder", "--help"])
    assert group.exit_code == 0, group.output
    assert "add" in group.output
    add = runner.invoke(main, ["folder", "add", "--help"])
    assert add.exit_code == 0, add.output
    assert "NAMESPACE=PATH" in add.output
    assert "--exclude" in add.output and "--provider" in add.output and "--host-config" in add.output
    assert "--catalog" not in add.output
    top = runner.invoke(main, ["--help"])
    assert top.exit_code == 0 and "folder" in top.output


def test_folder_add_partial_and_unsupported_only_are_honest(tmp_path: Path) -> None:
    folder = tmp_path / "selected"
    folder.mkdir()
    (folder / "good.md").write_text("# Good\n\nGOOD_TOKEN.\n", encoding="utf-8")
    (folder / "bad.txt").write_bytes(b"BAD_TOKEN \xff\xfe")
    (folder / "unknown.pdf").write_bytes(b"%PDF fake")
    runner = CliRunner()
    result = runner.invoke(main, [
        "--root", str(tmp_path), "folder", "add", f"fixture={folder}",
        "--provider", "synthetic",
    ])
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["data"]["status"] == "partial_success"
    assert payload["data"]["counts"] == {
        "added": 1,
        "updated": 0,
        "skipped": 1,
        "failed": 1,
        "unprocessed": 0,
        "excluded": 0,
        "total": 3,
    }
    assert "BAD_TOKEN" not in result.output
    assert str(folder) not in result.output

    only_unsupported = tmp_path / "only_unsupported"
    only_unsupported.mkdir()
    (only_unsupported / "document.pdf").write_bytes(b"%PDF fake")
    other_app = tmp_path / "other_app"
    other_app.mkdir()
    unsupported = runner.invoke(main, [
        "--root", str(other_app), "folder", "add",
        f"fixture={only_unsupported}", "--provider", "synthetic",
    ])
    assert unsupported.exit_code == 1
    data = json.loads(unsupported.output)["data"]
    assert data["status"] == "partial_success"
    assert data["counts"]["added"] == 0 and data["counts"]["skipped"] == 1


def test_folder_add_cli_exclusions_and_fixed_refusals(tmp_path: Path) -> None:
    folder = tmp_path / "selected"
    folder.mkdir()
    (folder / "keep.md").write_text("# Keep\n\nKEEP_TOKEN.\n", encoding="utf-8")
    (folder / "private").mkdir()
    (folder / "private" / "secret.md").write_text("SECRET_TOKEN.\n", encoding="utf-8")
    runner = CliRunner()
    excluded = runner.invoke(main, [
        "--root", str(tmp_path), "folder", "add", f"fixture={folder}",
        "--exclude", "private/**", "--provider", "synthetic",
    ])
    assert excluded.exit_code == 0, excluded.output
    data = json.loads(excluded.output)["data"]
    assert data["counts"]["added"] == 1 and data["counts"]["excluded"] == 1

    invalid = runner.invoke(main, [
        "--root", str(tmp_path), "folder", "add", "PRIVATE_SPEC_SENTINEL",
        "--provider", "synthetic",
    ])
    assert invalid.exit_code == 1
    error = json.loads(invalid.output)
    assert error["ok"] is False
    assert error["error"]["code"] == "FOLDER_IMPORT_ERROR"
    assert error["error"]["details"]["reason_code"] == "folder_selection_invalid"
    assert "PRIVATE_SPEC_SENTINEL" not in invalid.output

    other = tmp_path / "other"
    other.mkdir()
    (other / "other.md").write_text("# Other\n\nOTHER_TOKEN.\n", encoding="utf-8")
    remap = runner.invoke(main, [
        "--root", str(tmp_path), "folder", "add", f"fixture={other}",
        "--provider", "synthetic",
    ])
    assert remap.exit_code == 1
    assert json.loads(remap.output)["error"]["details"]["reason_code"] == "namespace_root_conflict"
    assert str(other) not in remap.output


def test_folder_add_refuses_derived_store_inside_selected_root(tmp_path: Path) -> None:
    selected = tmp_path / "selected"
    selected.mkdir()
    (selected / "note.md").write_text("# Note\n\nPRIVATE_CONTENT_SENTINEL.\n", encoding="utf-8")
    result = CliRunner().invoke(main, [
        "--root", str(selected), "folder", "add", f"fixture={selected}",
        "--provider", "synthetic",
    ])
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["error"]["details"]["reason_code"] == "source_overlaps_store"
    assert "PRIVATE_CONTENT_SENTINEL" not in result.output
    assert str(selected) not in result.output
    assert not (selected / ".mdrack").exists()


def test_folder_add_progress_logs_are_safe(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    folder = tmp_path / "PRIVATE_ROOT_SENTINEL"
    folder.mkdir()
    content_sentinel = "PRIVATE_CONTENT_SENTINEL"
    (folder / "notes.md").write_text(f"# Notes\n\n{content_sentinel}.\n", encoding="utf-8")
    with caplog.at_level(logging.INFO):
        result = CliRunner().invoke(main, [
            "--root", str(tmp_path), "folder", "add", f"fixture={folder}",
            "--provider", "synthetic",
        ])
    assert result.exit_code == 0, result.output
    assert "folder.file.finished" in caplog.text
    assert "folder.import.finished" in caplog.text
    assert "file_ref=" in caplog.text
    assert content_sentinel not in caplog.text
    assert str(folder) not in caplog.text
    assert "PRIVATE_ROOT_SENTINEL" not in result.output


def test_folder_add_keeps_configured_exclusions_without_cli_option(tmp_path: Path) -> None:
    folder = tmp_path / "selected"
    folder.mkdir()
    (folder / "keep.md").write_text("# Keep\n\nKEEP_TOKEN.\n", encoding="utf-8")
    (folder / "private").mkdir()
    (folder / "private" / "secret.md").write_text("SECRET_TOKEN.\n", encoding="utf-8")
    store = tmp_path / ".mdrack"
    store.mkdir()
    (store / "config.toml").write_text('[scan]\nexclude = ["private/**"]\n', encoding="utf-8")
    result = CliRunner().invoke(main, [
        "--root", str(tmp_path), "folder", "add", f"fixture={folder}",
        "--provider", "synthetic",
    ])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)["data"]
    assert data["counts"]["added"] == 1
    assert data["counts"]["excluded"] == 1
    assert [item["source_ref"] for item in data["files"]] == ["keep.md"]


def test_folder_add_missing_root_is_a_fixed_input_error(tmp_path: Path) -> None:
    missing = tmp_path / "PRIVATE_MISSING_ROOT_SENTINEL"
    result = CliRunner().invoke(main, [
        "--root", str(tmp_path), "folder", "add", f"fixture={missing}",
        "--provider", "synthetic",
    ])
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["error"]["details"]["reason_code"] == "source_root_unavailable"
    assert str(missing) not in result.output
