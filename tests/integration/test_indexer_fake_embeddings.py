"""Integration test for indexer with fake embeddings."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from mdrack.embeddings.fake import FakeEmbeddingProvider
from mdrack.indexing.indexer import run_indexer
from mdrack.storage.sqlite.connection import get_connection
from mdrack.storage.sqlite.repositories import count_chunks, count_files, get_file_by_path, list_sections

if TYPE_CHECKING:
    from mdrack.config.models import MDRackConfig


@pytest.fixture
def temp_root_with_docs(tmp_path: Path) -> Path:
    """Create a temporary directory with sample markdown files."""
    root = tmp_path / "vault"
    root.mkdir()

    # Create sample markdown files
    (root / "doc1.md").write_text("""# Document 1

## Introduction
This is the introduction section.

Some paragraph text here with enough content to form a chunk.

## Features
- Feature A
- Feature B
- Feature C

More details about features.
""")

    (root / "doc2.md").write_text("""# Document 2

No headings here, just a single section.
""")

    (root / "subdir").mkdir()
    (root / "subdir" / "nested.md").write_text("""# Nested Doc

## Deep Section
Content in a nested directory.
""")

    return root


def _get_config_with_store(tmp_path: Path) -> "MDRackConfig":
    """Get a config with store pointing to a temporary directory."""
    from mdrack.config.models import MDRackConfig, PathsConfig

    store_path = tmp_path / ".mdrack"
    paths = PathsConfig(root=".", store=str(store_path), config_file=".mdrack/config.toml")
    return MDRackConfig(paths=paths)


def test_indexer_with_fake_embeddings(temp_root_with_docs: Path):
    """Test full indexing pipeline using FakeEmbeddingProvider."""
    config = _get_config_with_store(temp_root_with_docs.parent)

    provider = FakeEmbeddingProvider(dimensions=128)

    # Run indexer
    result = run_indexer(temp_root_with_docs, config, provider=provider, profile="default")

    # Verify result stats
    assert result.files_seen == 3  # doc1.md, doc2.md, nested.md
    assert result.files_changed >= 3  # All files are new or changed
    assert result.files_deleted == 0
    assert result.chunks_created > 0
    assert result.errors_count == 0

    # Verify database contents
    db_path = Path(config.paths.store) / "knowledge.db"
    assert db_path.exists()

    conn = get_connection(db_path)
    try:
        # Check files were inserted
        files = conn.execute("SELECT * FROM files").fetchall()
        assert len(files) == 3

        # Check each file
        f1 = get_file_by_path(conn, "doc1.md")
        assert f1 is not None
        assert f1["title"] == "Document 1"
        assert f1["status"] == "active"

        f2 = get_file_by_path(conn, "doc2.md")
        assert f2 is not None
        assert f2["title"] == "Document 2"

        f3 = get_file_by_path(conn, "subdir/nested.md")
        assert f3 is not None
        assert f3["title"] == "Nested Doc"

        # Check sections
        sections = list_sections(conn, f1["id"])
        assert len(sections) == 2
        assert sections[0]["title"] == "Introduction"
        assert sections[1]["title"] == "Features"

        # Check chunks
        chunk_count = count_chunks(conn)
        assert chunk_count == result.chunks_created

        # Check embeddings
        from mdrack.storage.sqlite.repositories import count_embeddings
        emb_count = count_embeddings(conn, "default")
        assert emb_count == chunk_count

        # Check FTS index
        fts_count = conn.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0]
        assert fts_count == chunk_count

        # Verify FTS search works
        cursor = conn.execute("SELECT * FROM chunks_fts WHERE chunks_fts MATCH ?", ("intro*",))
        fts_results = cursor.fetchall()
        assert len(fts_results) > 0

    finally:
        conn.close()


def test_indexer_incremental_run(temp_root_with_docs: Path):
    """Test that running indexer twice doesn't duplicate data."""
    config = _get_config_with_store(temp_root_with_docs.parent)

    provider = FakeEmbeddingProvider(dimensions=128)

    # First run
    result1 = run_indexer(temp_root_with_docs, config, provider=provider)
    assert result1.files_changed == 3
    assert result1.chunks_created > 0

    # Second run (no changes)
    result2 = run_indexer(temp_root_with_docs, config, provider=provider)
    assert result2.files_seen == 3
    assert result2.files_changed == 0  # No changes
    assert result2.chunks_created == 0  # No new chunks
    assert result2.files_deleted == 0

    # Database should have same totals
    db_path = Path(config.paths.store) / "knowledge.db"
    conn = get_connection(db_path)
    try:
        files_count = count_files(conn)
        chunks_count = count_chunks(conn)
        assert files_count == 3
        assert chunks_count == result1.chunks_created
    finally:
        conn.close()


def test_indexer_with_file_modification(temp_root_with_docs: Path):
    """Test that changing a file triggers reindexing."""
    config = _get_config_with_store(temp_root_with_docs.parent)

    provider = FakeEmbeddingProvider(dimensions=128)

    # Initial run
    run_indexer(temp_root_with_docs, config, provider=provider)
    db_path = Path(config.paths.store) / "knowledge.db"
    initial_chunk_count = count_chunks(get_connection(db_path))

    # Modify a file
    doc1_path = temp_root_with_docs / "doc1.md"
    original_content = doc1_path.read_text()
    doc1_path.write_text(
        original_content
        + "\n\n## New Section\n"
        + "This is a new section with substantial content. " * 40
    )

    # Second run
    result2 = run_indexer(temp_root_with_docs, config, provider=provider)
    assert result2.files_changed == 1  # Only doc1.md changed
    assert result2.chunks_created > 0

    # Should have more chunks now
    conn = get_connection(db_path)
    try:
        new_chunk_count = count_chunks(conn)
        assert new_chunk_count > initial_chunk_count
    finally:
        conn.close()


def test_indexer_with_deleted_file(temp_root_with_docs: Path):
    """Test that deleting a file removes it from the index."""
    config = _get_config_with_store(temp_root_with_docs.parent)

    provider = FakeEmbeddingProvider(dimensions=128)

    # Initial run
    result1 = run_indexer(temp_root_with_docs, config, provider=provider)
    assert result1.files_changed == 3

    # Delete a file
    (temp_root_with_docs / "doc2.md").unlink()

    # Second run
    result2 = run_indexer(temp_root_with_docs, config, provider=provider)
    assert result2.files_deleted == 1  # doc2.md should be deleted
    assert result2.files_changed == 0  # Only 2 files remain, both unchanged

    # Verify file is gone
    db_path = Path(config.paths.store) / "knowledge.db"
    conn = get_connection(db_path)
    try:
        f2 = get_file_by_path(conn, "doc2.md")
        assert f2 is None

        files_count = count_files(conn)
        assert files_count == 2
    finally:
        conn.close()


def test_indexer_resolves_relative_store_against_root(temp_root_with_docs: Path):
    """Relative store paths should be created under the selected root."""
    from mdrack.config.models import MDRackConfig, PathsConfig

    config = MDRackConfig(
        paths=PathsConfig(root=".", store=".custom-store", config_file=".mdrack/config.toml")
    )

    result = run_indexer(temp_root_with_docs, config, provider=FakeEmbeddingProvider(dimensions=128))

    assert result.files_seen == 3
    assert (temp_root_with_docs / ".custom-store" / "knowledge.db").is_file()
