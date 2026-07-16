"""Integration tests for semantic search using FakeEmbeddingProvider."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from mdrack.embeddings.fake import FakeEmbeddingProvider
from mdrack.search.semantic import SemanticSearchResult, semantic_search
from mdrack.storage.sqlite.connection import get_connection
from mdrack.storage.sqlite.migrations import apply_migrations
from mdrack.storage.sqlite.vector import VectorIndex

MIGRATIONS_DIR = (
    Path(__file__).resolve().parents[2] / "src" / "mdrack" / "storage" / "sqlite" / "migrations"
)


@pytest.fixture()
def provider() -> FakeEmbeddingProvider:
    return FakeEmbeddingProvider(dimensions=128)


@pytest.fixture()
def seeded_db() -> tuple:
    """Create a temporary DB seeded with files, sections, chunks, and embeddings."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db_path = Path(tmp.name)
    conn = get_connection(db_path)
    apply_migrations(conn, MIGRATIONS_DIR)

    provider = FakeEmbeddingProvider(dimensions=128)
    profile = "default"
    conn.execute(
        "INSERT INTO embedding_profiles (name, model, dimensions) VALUES (?, ?, ?)",
        (profile, "fake-model", 128),
    )

    # File 1
    conn.execute(
        "INSERT INTO files (id, relative_path, title, source_hash, indexed_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("file-1", "docs/python.md", "Python Guide", "hash1", "2026-01-01T00:00:00"),
    )
    conn.execute(
        "INSERT INTO sections (id, file_id, title, heading_path, level, start_line, end_line) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("sec-1", "file-1", "Data Classes", "## Data Classes", 2, 10, 30),
    )

    # File 2
    conn.execute(
        "INSERT INTO files (id, relative_path, title, source_hash, indexed_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("file-2", "docs/rust.md", "Rust Guide", "hash2", "2026-01-01T00:00:00"),
    )
    conn.execute(
        "INSERT INTO sections (id, file_id, title, heading_path, level, start_line, end_line) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("sec-2", "file-2", "Ownership", "## Ownership", 2, 5, 25),
    )

    # Chunks with different content
    chunks = [
        ("chunk-1", "file-1", "sec-1", "Python dataclasses provide a concise way to create classes.", 0),
        ("chunk-2", "file-1", "sec-1", "Use the @dataclass decorator for automatic __init__ and __repr__.", 1),
        ("chunk-3", "file-2", "sec-2", "Rust ownership ensures memory safety without a garbage collector.", 0),
        ("chunk-4", "file-2", None, "Introduction to systems programming concepts.", 0),
    ]
    for cid, fid, sid, content, idx in chunks:
        conn.execute(
            "INSERT INTO chunks (id, file_id, section_id, content, content_type, chunk_index) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (cid, fid, sid, content, "text", idx),
        )
    conn.commit()

    # Generate and store embeddings for each chunk (sync helper avoids event-loop issues)
    texts = [c[3] for c in chunks]
    vectors = provider._text_to_vector_sync(texts)

    vi = VectorIndex(conn)
    for (cid, _, _, _, _), vec in zip(chunks, vectors):
        vi.upsert(cid, profile, vec)

    yield conn, provider, db_path
    conn.close()
    db_path.unlink(missing_ok=True)


@pytest.mark.asyncio()
async def test_basic_semantic_search(seeded_db: tuple) -> None:
    conn, provider, _ = seeded_db
    result = await semantic_search(conn, "dataclasses in Python", provider)

    assert isinstance(result, SemanticSearchResult)
    assert result.query == "dataclasses in Python"
    assert result.total_count > 0
    assert result.error is None
    assert all(isinstance(r.score, float) for r in result.results)


@pytest.mark.asyncio()
async def test_search_with_no_embeddings(seeded_db: tuple) -> None:
    conn, provider, _ = seeded_db
    # Remove all embeddings
    conn.execute("DELETE FROM chunk_embeddings")
    conn.commit()

    result = await semantic_search(conn, "dataclasses", provider)

    assert result.total_count == 0
    assert result.results == []
    assert result.error is None


@pytest.mark.asyncio()
async def test_nearest_neighbor_is_correct(seeded_db: tuple) -> None:
    """Top result for identical query text should score ~1.0 (exact match)."""
    conn, provider, _ = seeded_db
    # Search with exact content of chunk-1 to verify exact-match ranking
    result = await semantic_search(
        conn, "Python dataclasses provide a concise way to create classes.", provider, limit=4
    )

    assert result.total_count == 4
    assert result.results[0].chunk_id == "chunk-1"
    assert result.results[0].score == pytest.approx(1.0)


@pytest.mark.asyncio()
async def test_results_include_file_path_and_section_title(seeded_db: tuple) -> None:
    """Every result must carry file_relative_path and correct section provenance."""
    conn, provider, _ = seeded_db
    result = await semantic_search(conn, "dataclasses Python", provider, limit=4)

    assert result.total_count == 4
    for item in result.results:
        assert isinstance(item.file_relative_path, str)
        assert item.file_relative_path.startswith("docs/")

    # Find the result for chunk-1 and verify its provenance
    c1 = next(r for r in result.results if r.chunk_id == "chunk-1")
    assert c1.file_relative_path == "docs/python.md"
    assert c1.section_title == "Data Classes"
    assert c1.heading_path == "## Data Classes"


@pytest.mark.asyncio()
async def test_empty_query_returns_nothing(seeded_db: tuple) -> None:
    conn, provider, _ = seeded_db
    result = await semantic_search(conn, "   ", provider)
    assert result.total_count == 0
    assert result.results == []


@pytest.mark.asyncio()
async def test_limit_is_respected(seeded_db: tuple) -> None:
    conn, provider, _ = seeded_db
    result = await semantic_search(conn, "programming", provider, limit=2)
    assert result.total_count <= 2


@pytest.mark.asyncio()
async def test_embedding_error_returns_structured_error() -> None:
    """Provider that raises EmbeddingError produces a structured error result."""
    import sqlite3

    from mdrack.embeddings.protocol import EmbeddingError

    class FailingProvider:
        dimensions = 128

        async def embed(self, texts, profile="default"):
            raise EmbeddingError("LM Studio unavailable")

        async def embed_query(self, text, profile="default"):
            raise EmbeddingError("LM Studio unavailable")

        async def health(self):
            from mdrack.embeddings.protocol import EmbeddingHealth
            return EmbeddingHealth(ok=False, provider="fail", model="fail", dimensions=0, error="down")

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_migrations(conn, MIGRATIONS_DIR)

    result = await semantic_search(conn, "test query", FailingProvider())  # type: ignore[arg-type]

    assert result.total_count == 0
    assert result.error is not None
    assert result.error == "embedding_provider_error"


@pytest.mark.asyncio()
async def test_result_scores_are_descending(seeded_db: tuple) -> None:
    conn, provider, _ = seeded_db
    result = await semantic_search(conn, "ownership memory safety", provider, limit=4)

    scores = [r.score for r in result.results]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.asyncio()
async def test_chunk_without_section_gets_none(seeded_db: tuple) -> None:
    conn, provider, _ = seeded_db
    result = await semantic_search(conn, "systems programming", provider, limit=1)

    assert result.total_count >= 1
    chunk4_item = next((r for r in result.results if r.chunk_id == "chunk-4"), None)
    if chunk4_item is not None:
        assert chunk4_item.section_title is None
        assert chunk4_item.file_relative_path == "docs/rust.md"
