"""SQLite asset registry and reference persistence contracts."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from mdrack.adapters.sqlite.index_storage import SQLiteIndexStorage
from mdrack.domain.assets import Asset, AssetReference
from mdrack.domain.blocks import SourceSpan
from mdrack.domain.indexing import PreparedFile, StoredChunk, StoredSection
from mdrack.storage.sqlite.connection import get_connection
from mdrack.storage.sqlite.migrations import apply_migrations, get_migrations_dir


def _prepared(*, source_hash: str = "source-hash") -> PreparedFile:
    section = StoredSection("section", "section-logical", "Title", ("Title",), 1, 1, 2, None)
    chunk = StoredChunk(
        "chunk", "chunk-logical", "section", "Architecture", "image_reference", 0,
        ("Title",), None, None, "Architecture context", "embedding-hash", 2, 2, "block-logical",
    )
    asset = Asset(
        asset_id="asset-logical",
        root_id="default",
        relative_path="images/diagram.png",
        content_hash="asset-hash",
        mime_type="image/png",
        size_bytes=12,
        width=2,
        height=3,
        exists=True,
    )
    reference = AssetReference(
        reference_id="reference-logical",
        asset_id=asset.asset_id,
        document_id="file-logical",
        document_relative_path="note.md",
        block_id="block-logical",
        chunk_id="chunk-logical",
        raw_reference="images/diagram.png",
        syntax="markdown",
        source_span=SourceSpan(2, 2, 10, 45),
        alt_text="Architecture",
        surrounding_text="Context",
        resolution_status="resolved",
    )
    return PreparedFile(
        record_id="file", logical_id="file-logical", root_id="default", relative_path="note.md",
        title="Title", source_hash=source_hash, indexed_at="2026-01-01T00:00:00Z",
        parser_name="test", parser_version="1", chunk_strategy_name="test",
        chunk_strategy_version="1", index_run_id="run", sections=(section,), chunks=(chunk,),
        assets=(asset,), asset_references=(reference,),
    )


def _storage(tmp_path: Path) -> tuple[SQLiteIndexStorage, sqlite3.Connection]:
    conn = get_connection(tmp_path / "knowledge.db")
    apply_migrations(conn, get_migrations_dir())
    conn.execute("INSERT INTO index_runs (id, started_at, status) VALUES ('run', '2026-01-01', 'running')")
    conn.commit()
    return SQLiteIndexStorage(conn), conn


def test_asset_registry_and_reference_are_atomic_and_queryable(tmp_path: Path) -> None:
    storage, conn = _storage(tmp_path)
    storage.replace_file(_prepared())

    assets = storage.list_assets_for_file("note.md")
    references = storage.list_asset_references("note.md")
    assert assets[0]["asset_id"] == "asset-logical"
    assert assets[0]["relative_path"] == "images/diagram.png"
    assert references[0]["raw_reference"] == "images/diagram.png"
    assert references[0]["start_offset"] == 10
    assert references[0]["end_offset"] == 45
    assert references[0]["chunk_logical_id"] == "chunk-logical"


def test_reindex_replaces_references_and_removes_orphan_assets(tmp_path: Path) -> None:
    storage, conn = _storage(tmp_path)
    storage.replace_file(_prepared())
    replacement = replace(_prepared(source_hash="new-hash"), assets=(), asset_references=())

    storage.replace_file(replacement)

    assert conn.execute("SELECT COUNT(*) FROM asset_references").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 0


def test_failed_asset_replacement_rolls_back_previous_graph(tmp_path: Path) -> None:
    storage, conn = _storage(tmp_path)
    original = _prepared()
    storage.replace_file(original)
    invalid = replace(original, source_hash="new-hash", assets=())

    with pytest.raises(sqlite3.IntegrityError):
        storage.replace_file(invalid)

    assert conn.execute("SELECT source_hash FROM files WHERE id = 'file'").fetchone()[0] == "source-hash"
    assert conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM asset_references").fetchone()[0] == 1
