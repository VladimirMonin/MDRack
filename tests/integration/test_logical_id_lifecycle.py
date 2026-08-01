"""Logical identity lifecycle through the fixed canonical catalog."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from click.testing import CliRunner

from mdrack.application.compatibility import create_application_storage
from mdrack.application.indexing import IndexingService
from mdrack.cli import main
from mdrack.config.models import MDRackConfig, PathsConfig
from mdrack.domain.indexing import IndexingResult
from mdrack.embeddings.fake import FakeEmbeddingProvider


def _config(root: Path) -> MDRackConfig:
    return MDRackConfig(paths=PathsConfig(root=".", store=str(root / ".mdrack")))


def _scan(root: Path, config: MDRackConfig, *, force: bool = False) -> IndexingResult:
    service = IndexingService(
        root,
        config,
        create_application_storage(root, config, create=True),
        provider=FakeEmbeddingProvider(dimensions=8),
        root_id="lifecycle",
    )
    try:
        return service.scan(force_reindex=force)
    finally:
        service.close()


def _file_record(root: Path, config: MDRackConfig, relative_path: str) -> dict[str, Any] | None:
    storage = create_application_storage(root, config)
    try:
        record = storage.get_file_by_path(relative_path)
        return None if record is None else dict(record)
    finally:
        storage.close()


def _units(root: Path, config: MDRackConfig, resource_id: str) -> list[dict[str, Any]]:
    storage = create_application_storage(root, config)
    try:
        return [
            dict(row)
            for row in storage.connection.execute(
                "SELECT unit_id, text_content, evidence_locator_json "
                "FROM core_search_units WHERE resource_id=? AND unit_kind='text_chunk' ORDER BY unit_id",
                (resource_id,),
            ).fetchall()
        ]
    finally:
        storage.close()


def test_logical_ids_survive_reindex_edit_move_and_cleanup(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    (root / "images").mkdir(parents=True)
    note = root / "note.md"
    image = root / "images" / "diagram.png"
    image.write_bytes(b"offline-image")
    note.write_text(
        "# Lifecycle\n\n## Change\n\nOld beta.\n\n![Diagram](images/diagram.png)\n\n"
        "## Keep\n\nStable alpha.\n",
        encoding="utf-8",
    )
    other = root / "other.md"
    other.write_text("# Other\n\n## Keep\n\nStable alpha.\n", encoding="utf-8")
    config = _config(root)

    assert _scan(root, config).status == "success"
    note_record = _file_record(root, config, "note.md")
    other_record = _file_record(root, config, "other.md")
    assert note_record is not None and other_record is not None
    note_units = _units(root, config, str(note_record["logical_id"]))
    other_units = _units(root, config, str(other_record["logical_id"]))
    keep_before = next(unit for unit in note_units if "Stable alpha" in str(unit["text_content"]))
    assert keep_before["unit_id"] not in {unit["unit_id"] for unit in other_units}

    assert _scan(root, config, force=True).status == "success"
    assert _units(root, config, str(note_record["logical_id"])) == note_units

    note.write_text(
        "# Lifecycle\n\n## Change\n\nNew beta.\nAdditional line.\n\n"
        "## Keep\n\nStable alpha.\n",
        encoding="utf-8",
    )
    assert _scan(root, config).status == "success"
    after_edit = _units(root, config, str(note_record["logical_id"]))
    assert any(unit["unit_id"] == keep_before["unit_id"] for unit in after_edit)
    assert all("Diagram" not in str(unit["text_content"]) for unit in after_edit)

    moved = root / "moved" / "renamed.md"
    moved.parent.mkdir()
    note.rename(moved)
    ids_before_move = {unit["unit_id"] for unit in after_edit}
    assert _scan(root, config).status == "success"
    moved_record = _file_record(root, config, "moved/renamed.md")
    assert moved_record is not None
    assert moved_record["logical_id"] == note_record["logical_id"]
    assert {unit["unit_id"] for unit in _units(root, config, str(moved_record["logical_id"]))} == ids_before_move

    moved.unlink()
    assert _scan(root, config).status == "success"
    assert _file_record(root, config, "moved/renamed.md") is None


def test_cli_reads_chunk_by_logical_id_without_storage_identifiers(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "duplicate.md").write_text(
        "# Duplicate\n\n## Same\n\nFirst.\n\n## Same\n\nSecond.\n",
        encoding="utf-8",
    )
    config = _config(root)
    assert _scan(root, config).status == "success"
    record = _file_record(root, config, "duplicate.md")
    assert record is not None
    chunk = next(
        unit for unit in _units(root, config, str(record["logical_id"])) if "Second" in str(unit["text_content"])
    )

    result = CliRunner().invoke(
        main,
        ["--root", str(root), "read", "chunk", str(chunk["unit_id"]), "--context", "neighbors"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    public_chunk = payload["data"]["chunk"]
    assert public_chunk["id"] == chunk["unit_id"]
    assert public_chunk["logical_id"] == chunk["unit_id"]
    assert "record_id" not in public_chunk
    assert "file_id" not in public_chunk
    assert "section_id" not in public_chunk
