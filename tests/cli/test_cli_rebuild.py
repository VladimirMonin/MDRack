"""Rebuild commands operate only on the fixed application catalog."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from mdrack.adapters.sqlite.canonical_catalog import open_application_catalog_readonly
from mdrack.cli import main
from mdrack.cli.commands.rebuild import rebuild_embeddings_in_db
from mdrack.config.models import MDRackConfig


def _prepare_catalog(runner: CliRunner, root: Path) -> None:
    (root / "note.md").write_text("# Rebuild\n\nCanonical catalog rebuild coverage.", encoding="utf-8")
    initialized = runner.invoke(main, ["--root", str(root), "init"])
    scanned = runner.invoke(main, ["--root", str(root), "scan", "--provider", "fake"])
    assert initialized.exit_code == 0, initialized.output
    assert scanned.exit_code == 0, scanned.output


def _payload(result) -> dict[str, object]:
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    return payload


def test_rebuild_fts_uses_one_fixed_catalog_and_is_idempotent(tmp_path: Path) -> None:
    runner = CliRunner()
    _prepare_catalog(runner, tmp_path)

    first = _payload(runner.invoke(main, ["--root", str(tmp_path), "rebuild", "fts"]))
    second = _payload(runner.invoke(main, ["--root", str(tmp_path), "rebuild", "fts"]))

    first_data = first["data"]
    second_data = second["data"]
    assert isinstance(first_data, dict)
    assert isinstance(second_data, dict)
    assert first_data["chunk_count"] > 0
    assert first_data["fts_count"] == first_data["chunk_count"]
    assert second_data == first_data
    assert (tmp_path / ".mdrack" / "catalog.sqlite3").is_file()
    assert not (tmp_path / ".mdrack" / "knowledge.db").exists()


def test_rebuild_embeddings_reindexes_the_fixed_catalog(tmp_path: Path) -> None:
    runner = CliRunner()
    _prepare_catalog(runner, tmp_path)

    payload = _payload(
        runner.invoke(
            main,
            ["--root", str(tmp_path), "rebuild", "embeddings", "--provider", "fake"],
        )
    )

    data = payload["data"]
    assert isinstance(data, dict)
    assert data["provider"] == "fake"
    assert data["profile"] == "default"
    assert data["embedded_count"] == data["total_chunks"]
    assert data["embedded_count"] > 0
    with open_application_catalog_readonly(tmp_path, MDRackConfig()) as catalog:
        chunk_vector_count = catalog.connection.execute(
            "SELECT COUNT(*) FROM core_unit_embeddings e "
            "JOIN core_search_units u USING(unit_id) WHERE u.unit_kind='text_chunk'"
        ).fetchone()[0]
    assert chunk_vector_count == data["embedded_count"]


def test_direct_database_rebuild_is_retired_before_opening_a_second_store(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="legacy_embedding_rebuild_unsupported"):
        rebuild_embeddings_in_db(tmp_path / "another.sqlite3", object())  # type: ignore[arg-type]
