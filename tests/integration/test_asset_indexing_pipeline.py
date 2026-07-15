"""End-to-end offline asset indexing without source mutation or model calls."""

from __future__ import annotations

import hashlib
from pathlib import Path

from mdrack.adapters.sqlite.index_storage import create_sqlite_index_storage
from mdrack.application.indexing import IndexingService
from mdrack.config.models import MDRackConfig, PathsConfig


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_asset_pipeline_persists_provenance_and_searches_only_text(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    (root / "images").mkdir(parents=True)
    note = root / "note.md"
    image = root / "images" / "private-name.png"
    note.write_text("Context before.\n![Architecture](images/private-name.png)\nContext after.\n", encoding="utf-8")
    image.write_bytes(b"not-a-real-image")
    before = {path: _sha256(path) for path in (note, image)}
    config = MDRackConfig(paths=PathsConfig(root=".", store=str(tmp_path / ".store")))
    storage = create_sqlite_index_storage(root, config)
    service = IndexingService(root, config, storage, provider=None)

    result = service.scan(force_reindex=True)
    assets = storage.list_assets_for_file("note.md")
    references = storage.list_asset_references("note.md")
    search = storage.search_text("Architecture", limit=5)
    service.close()

    assert result.status == "success"
    assert len(assets) == len(references) == 1
    assert references[0]["start_line"] == references[0]["end_line"] == 2
    assert search.total_count == 1
    assert "private-name.png" not in search.results[0].snippet
    assert {path: _sha256(path) for path in (note, image)} == before
