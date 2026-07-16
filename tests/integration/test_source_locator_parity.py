"""Complete normalized source locators across retrieval and read surfaces."""

from __future__ import annotations

import asyncio
from pathlib import Path

from mdrack.adapters.sqlite.index_storage import create_sqlite_index_storage
from mdrack.application.indexing import IndexingService
from mdrack.application.retrieval import RetrievalService
from mdrack.config.models import MDRackConfig, PathsConfig
from mdrack.embeddings.fake import FakeEmbeddingProvider
from mdrack.public_api import MDRackEngine


def test_text_semantic_and_embedded_read_share_complete_locator(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "guide.md").write_text(
        "# Guide\n\n## Stable\n\nExact provenance phrase.\n",
        encoding="utf-8",
    )
    config = MDRackConfig(paths=PathsConfig(root=".", store=str(tmp_path / ".store")))
    provider = FakeEmbeddingProvider(dimensions=8)
    storage = create_sqlite_index_storage(root, config)
    service = IndexingService(root, config, storage, provider=provider, root_id="parity")
    assert service.scan().status == "success"

    retrieval = RetrievalService(storage, embedding_provider=provider)
    text_item = retrieval.search_text("provenance", limit=1).results[0]
    semantic_item = asyncio.run(retrieval.search_semantic("provenance", limit=1)).results[0]
    read_locator = storage.get_chunk_source_locator(text_item.logical_id)
    engine = MDRackEngine(root=root, config=config, storage=storage, embedding_provider=provider)
    embedded_locator = engine.get_chunk_source_locator(text_item.logical_id)

    assert text_item.source_locator == semantic_item.source_locator == read_locator == embedded_locator
    locator = read_locator.to_dict()
    assert set(locator) == {
        "root_id",
        "relative_path",
        "heading_path",
        "start_line",
        "end_line",
        "start_offset",
        "end_offset",
        "block_kind",
        "chunk_kind",
        "block_logical_id",
        "chunk_logical_id",
    }
    assert locator["relative_path"] == "guide.md"
    assert locator["heading_path"] == ["Guide", "Stable"]
    assert isinstance(locator["start_offset"], int)
    assert isinstance(locator["end_offset"], int)
    assert locator["end_offset"] > locator["start_offset"]
    assert locator["block_kind"] == "paragraph"
    assert locator["chunk_kind"] == "text"
    assert locator["chunk_logical_id"] == text_item.logical_id
    engine.close()
