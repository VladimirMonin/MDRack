"""Canonical catalog indexing contracts with deterministic fake embeddings."""

from __future__ import annotations

from pathlib import Path

import pytest

from mdrack.application.compatibility import create_application_storage
from mdrack.config.models import MDRackConfig, PathsConfig
from mdrack.embeddings.fake import FakeEmbeddingProvider
from mdrack.indexing.indexer import run_indexer


@pytest.fixture
def temp_root_with_docs(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "doc1.md").write_text(
        "# Document 1\n\n## Introduction\nThis is the introduction section.\n\n"
        "Some paragraph text here with enough content to form a chunk.\n\n"
        "## Features\n- Feature A\n- Feature B\n- Feature C\n\nMore details about features.\n",
        encoding="utf-8",
    )
    (root / "doc2.md").write_text("# Document 2\n\nNo headings here, just a single section.\n", encoding="utf-8")
    (root / "subdir").mkdir()
    (root / "subdir" / "nested.md").write_text(
        "# Nested Doc\n\n## Deep Section\nContent in a nested directory.\n",
        encoding="utf-8",
    )
    return root


def _config(root: Path, store: str = ".mdrack") -> MDRackConfig:
    return MDRackConfig(paths=PathsConfig(root=".", store=store, config_file=".mdrack/config.toml"))


def _open_catalog(root: Path, config: MDRackConfig):
    return create_application_storage(root, config)


def _counts(storage) -> tuple[int, int, int]:
    return tuple(
        int(storage.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in ("core_resources", "core_search_units", "core_unit_embeddings")
    )


def test_indexer_with_fake_embeddings_projects_docs_into_one_catalog(temp_root_with_docs: Path) -> None:
    config = _config(temp_root_with_docs)
    result = run_indexer(temp_root_with_docs, config, provider=FakeEmbeddingProvider(dimensions=128))

    assert result.files_seen == 3
    assert result.files_changed == 3
    assert result.files_deleted == 0
    assert result.chunks_created > 0
    assert result.errors_count == 0
    assert (temp_root_with_docs / ".mdrack" / "catalog.sqlite3").is_file()
    assert not (temp_root_with_docs / ".mdrack" / "knowledge.db").exists()

    storage = _open_catalog(temp_root_with_docs, config)
    try:
        assert storage.count_public_files() == 3
        document = storage.get_file_by_path("doc1.md")
        assert document is not None
        assert document["title"] == "Document 1"
        assert document["status"] == "active"
        nested = storage.get_file_by_path("subdir/nested.md")
        assert nested is not None
        assert nested["title"] == "Nested Doc"
        resource_count, unit_count, vector_count = _counts(storage)
        assert resource_count == 3
        assert unit_count > result.chunks_created
        assert vector_count == unit_count
        candidates = storage.retrieve_text_candidates("introduction", limit=5)
        assert candidates
        assert candidates[0].source_locator.relative_path == "doc1.md"
    finally:
        storage.close()


def test_indexer_incremental_run_does_not_duplicate_canonical_records(temp_root_with_docs: Path) -> None:
    config = _config(temp_root_with_docs)
    provider = FakeEmbeddingProvider(dimensions=128)
    first = run_indexer(temp_root_with_docs, config, provider=provider)
    storage = _open_catalog(temp_root_with_docs, config)
    try:
        before = _counts(storage)
    finally:
        storage.close()

    second = run_indexer(temp_root_with_docs, config, provider=provider)
    storage = _open_catalog(temp_root_with_docs, config)
    try:
        after = _counts(storage)
    finally:
        storage.close()

    assert first.files_changed == 3
    assert second.files_seen == 3
    assert second.files_changed == 0
    assert second.chunks_created == 0
    assert second.files_deleted == 0
    assert after == before


def test_indexer_replaces_changed_document_projection(temp_root_with_docs: Path) -> None:
    config = _config(temp_root_with_docs)
    provider = FakeEmbeddingProvider(dimensions=128)
    run_indexer(temp_root_with_docs, config, provider=provider)
    storage = _open_catalog(temp_root_with_docs, config)
    try:
        before = _counts(storage)[1]
    finally:
        storage.close()

    document = temp_root_with_docs / "doc1.md"
    document.write_text(
        document.read_text(encoding="utf-8")
        + "\n\n## New Section\n"
        + "This is a new section with substantial content. " * 40,
        encoding="utf-8",
    )
    result = run_indexer(temp_root_with_docs, config, provider=provider)
    storage = _open_catalog(temp_root_with_docs, config)
    try:
        after = _counts(storage)[1]
        candidates = storage.retrieve_text_candidates("substantial", limit=5)
    finally:
        storage.close()

    assert result.files_changed == 1
    assert result.chunks_created > 0
    assert after > before
    assert candidates and candidates[0].source_locator.relative_path == "doc1.md"


def test_indexer_deletes_removed_document_from_canonical_catalog(temp_root_with_docs: Path) -> None:
    config = _config(temp_root_with_docs)
    provider = FakeEmbeddingProvider(dimensions=128)
    first = run_indexer(temp_root_with_docs, config, provider=provider)
    assert first.files_changed == 3
    (temp_root_with_docs / "doc2.md").unlink()

    result = run_indexer(temp_root_with_docs, config, provider=provider)
    storage = _open_catalog(temp_root_with_docs, config)
    try:
        assert storage.get_file_by_path("doc2.md") is None
        assert storage.count_public_files() == 2
    finally:
        storage.close()

    assert result.files_deleted == 1
    assert result.files_changed == 0


def test_indexer_resolves_relative_store_against_root(temp_root_with_docs: Path) -> None:
    config = _config(temp_root_with_docs, ".custom-store")

    result = run_indexer(temp_root_with_docs, config, provider=FakeEmbeddingProvider(dimensions=128))

    assert result.files_seen == 3
    assert (temp_root_with_docs / ".custom-store" / "catalog.sqlite3").is_file()
