"""Stable public logical identities across the index lifecycle."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from mdrack.adapters.sqlite.index_storage import create_sqlite_index_storage
from mdrack.application.indexing import IndexingService
from mdrack.cli import main
from mdrack.config.models import MDRackConfig, PathsConfig
from mdrack.embeddings.fake import FakeEmbeddingProvider


def _config() -> MDRackConfig:
    return MDRackConfig(paths=PathsConfig(root=".", store=".mdrack"))


def _rows(storage, sql: str) -> list[dict[str, object]]:
    return [dict(row) for row in storage.connection.execute(sql).fetchall()]


def test_logical_ids_survive_reindex_edit_and_move_and_cleanup_stale_rows(
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    (root / "images").mkdir(parents=True)
    note = root / "note.md"
    image = root / "images" / "diagram.png"
    image.write_bytes(b"offline-image")
    note.write_text(
        "# Lifecycle\n\n"
        "## Change\n\nOld beta.\n\n![Diagram](images/diagram.png)\n\n"
        "## Keep\n\nStable alpha.\n",
        encoding="utf-8",
    )
    other = root / "other.md"
    other.write_text("# Other\n\n## Keep\n\nStable alpha.\n", encoding="utf-8")
    storage = create_sqlite_index_storage(root, _config())
    service = IndexingService(
        root,
        _config(),
        storage,
        provider=FakeEmbeddingProvider(dimensions=8),
        root_id="lifecycle",
    )

    assert service.scan().status == "success"
    files_before = _rows(storage, "SELECT id, logical_id, relative_path FROM files ORDER BY relative_path")
    chunks_before = _rows(
        storage,
        "SELECT id, logical_id, content, file_id FROM chunks ORDER BY file_id, chunk_index",
    )
    note_file = next(row for row in files_before if row["relative_path"] == "note.md")
    note_chunks = [row for row in chunks_before if row["file_id"] == note_file["id"]]
    keep_before = next(row for row in note_chunks if "Stable alpha" in str(row["content"]))
    image_before = next(row for row in note_chunks if "Diagram" in str(row["content"]))
    other_keep = next(
        row
        for row in chunks_before
        if "Stable alpha" in str(row["content"]) and row["file_id"] != note_file["id"]
    )
    assert keep_before["logical_id"] != other_keep["logical_id"]

    assert service.scan(force_reindex=True).status == "success"
    assert _rows(storage, "SELECT logical_id, content FROM chunks ORDER BY logical_id") == [
        {"logical_id": row["logical_id"], "content": row["content"]}
        for row in sorted(chunks_before, key=lambda item: str(item["logical_id"]))
    ]

    note.write_text(
        "# Lifecycle\n\n"
        "## Change\n\nNew beta.\nAdditional line.\n\n"
        "## Keep\n\nStable alpha.\n",
        encoding="utf-8",
    )
    assert service.scan().status == "success"
    keep_after_edit = storage.connection.execute(
        "SELECT logical_id FROM chunks WHERE content LIKE '%Stable alpha%' "
        "AND file_id = (SELECT id FROM files WHERE relative_path = 'note.md')"
    ).fetchone()
    assert keep_after_edit["logical_id"] == keep_before["logical_id"]
    assert storage.connection.execute(
        "SELECT COUNT(*) FROM chunks WHERE logical_id = ?", (image_before["logical_id"],)
    ).fetchone()[0] == 0
    assert storage.connection.execute(
        "SELECT COUNT(*) FROM chunk_embeddings WHERE chunk_id = ?", (image_before["id"],)
    ).fetchone()[0] == 0
    assert storage.connection.execute("SELECT COUNT(*) FROM asset_references").fetchone()[0] == 0
    assert storage.connection.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 0
    assert storage.connection.execute(
        "SELECT COUNT(*) FROM chunks_fts WHERE chunk_id = ?", (image_before["id"],)
    ).fetchone()[0] == 0

    moved = root / "moved" / "renamed.md"
    moved.parent.mkdir()
    note.rename(moved)
    ids_before_move = {
        row["logical_id"]
        for row in _rows(
            storage,
            "SELECT logical_id FROM chunks WHERE file_id = "
            "(SELECT id FROM files WHERE relative_path = 'note.md')",
        )
    }
    assert service.scan().status == "success"
    moved_file = storage.connection.execute(
        "SELECT id, logical_id FROM files WHERE relative_path = 'moved/renamed.md'"
    ).fetchone()
    assert moved_file["id"] == note_file["id"]
    assert moved_file["logical_id"] == note_file["logical_id"]
    assert {
        row["logical_id"]
        for row in storage.connection.execute(
            "SELECT logical_id FROM chunks WHERE file_id = ?", (moved_file["id"],)
        ).fetchall()
    } == ids_before_move

    moved.unlink()
    assert service.scan().status == "success"
    assert storage.connection.execute("SELECT COUNT(*) FROM files WHERE id = ?", (moved_file["id"],)).fetchone()[0] == 0
    assert storage.connection.execute(
        "SELECT COUNT(*) FROM chunks WHERE file_id = ?", (moved_file["id"],)
    ).fetchone()[0] == 0
    service.close()


def test_cli_reads_indexed_entities_by_logical_id_without_exposing_record_uuids(
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "duplicate.md").write_text(
        "# Duplicate\n\n## Same\n\nFirst.\n\n## Same\n\nSecond.\n",
        encoding="utf-8",
    )
    storage = create_sqlite_index_storage(root, _config())
    service = IndexingService(root, _config(), storage, root_id="public")
    assert service.scan().status == "success"
    chunk = storage.connection.execute(
        "SELECT id, logical_id FROM chunks WHERE content LIKE '%Second%'"
    ).fetchone()
    section_ids = [
        row["logical_id"]
        for row in storage.connection.execute(
            "SELECT logical_id FROM sections WHERE heading_path LIKE '%Same%' ORDER BY start_line"
        ).fetchall()
    ]
    assert len(section_ids) == len(set(section_ids)) == 2
    service.close()

    result = CliRunner().invoke(
        main,
        ["--root", str(root), "read", "chunk", chunk["logical_id"], "--context", "neighbors"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    public_chunk = payload["data"]["chunk"]
    assert public_chunk["id"] == chunk["logical_id"]
    assert public_chunk["logical_id"] == chunk["logical_id"]
    assert chunk["id"] not in json.dumps(payload)
    assert "file_id" not in public_chunk
    assert "section_id" not in public_chunk
