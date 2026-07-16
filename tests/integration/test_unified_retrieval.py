"""Integration coverage for the canonical application retrieval path."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mdrack.adapters.sqlite.index_storage import SQLiteIndexStorage
from mdrack.application.retrieval import RetrievalService
from mdrack.embeddings.fake import FakeEmbeddingProvider
from mdrack.storage.sqlite.connection import get_connection
from mdrack.storage.sqlite.migrations import apply_migrations

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "src" / "mdrack" / "storage" / "sqlite" / "migrations"


@pytest.fixture()
def retrieval_stack(tmp_path: Path):
    connection = get_connection(tmp_path / "knowledge.db")
    apply_migrations(connection, MIGRATIONS_DIR)
    provider = FakeEmbeddingProvider(dimensions=16)
    connection.execute(
        "INSERT INTO embedding_profiles (name, model, dimensions) VALUES (?, ?, ?)",
        ("default", "fake", 16),
    )
    connection.execute(
        "INSERT INTO files (id, logical_id, root_id, relative_path, source_hash, indexed_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("file-record", "file_logical", "root", "docs/guide.md", "hash", "2026-01-01T00:00:00Z"),
    )
    chunks = [
        ("record-a", "chunk_a", "block_a", "Python retrieval guide", 3, 5),
        ("record-b", "chunk_b", "block_b", "SQLite storage guide", 7, 9),
    ]
    vectors = provider._text_to_vector_sync([chunk[3] for chunk in chunks])
    for (record_id, logical_id, block_id, content, start_line, end_line), vector in zip(chunks, vectors):
        connection.execute(
            "INSERT INTO chunks "
            "(id, logical_id, file_id, content, content_type, chunk_index, heading_path, "
            "start_line, end_line, block_logical_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record_id,
                logical_id,
                "file-record",
                content,
                "text",
                0,
                json.dumps(["Guide"]),
                start_line,
                end_line,
                block_id,
            ),
        )
        connection.execute(
            "INSERT INTO chunks_fts (chunk_id, content, content_type, heading_path) VALUES (?, ?, ?, ?)",
            (record_id, content, "text", "Guide"),
        )
        connection.execute(
            "INSERT INTO chunk_embeddings (chunk_id, profile_name, embedding, embedded_at) VALUES (?, ?, ?, ?)",
            (record_id, "default", json.dumps(vector).encode("utf-8"), "2026-01-01T00:00:00Z"),
        )
    connection.commit()
    storage = SQLiteIndexStorage(connection)
    service = RetrievalService(storage, embedding_provider=provider, profile="default", rrf_k=60)
    yield service
    connection.close()


@pytest.mark.asyncio
async def test_text_semantic_and_hybrid_share_one_public_result_contract(retrieval_stack: RetrievalService) -> None:
    text = retrieval_stack.search_text("Python", limit=10)
    semantic = await retrieval_stack.search_semantic("Python retrieval guide", limit=10)
    hybrid = await retrieval_stack.search_hybrid("Python", limit=10, reranker=None)

    assert text.mode == "text"
    assert semantic.mode == "semantic"
    assert hybrid.mode == "hybrid"
    assert text.results[0].logical_id == "chunk_a"
    assert semantic.results[0].logical_id == "chunk_a"
    assert all(item.rerank_rank is None and item.rerank_score is None for item in hybrid.results)
    assert all(item.source_locator.chunk_id == item.logical_id for item in hybrid.results)
    assert hybrid.results[0].rrf_rank == 1
    for result in (text, semantic, hybrid):
        serialized_item = result.to_dict()["results"][0]
        assert isinstance(serialized_item, dict)
        assert serialized_item["heading_path"] == ["Guide"]
        assert serialized_item["heading_path"] == serialized_item["source_locator"]["heading_path"]
