"""Integration tests for VectorIndex (pure-Python cosine similarity)."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from mdrack.storage.sqlite.connection import get_connection
from mdrack.storage.sqlite.migrations import apply_migrations
from mdrack.storage.sqlite.vector import VectorIndex

MIGRATIONS_DIR = (
    Path(__file__).resolve().parents[2] / "src" / "mdrack" / "storage" / "sqlite" / "migrations"
)


@pytest.fixture()
def conn_and_index() -> tuple[sqlite3.Connection, VectorIndex]:
    """Create a temporary database with schema applied and return (conn, index)."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db_path = Path(tmp.name)
    conn = get_connection(db_path)
    apply_migrations(conn, MIGRATIONS_DIR)
    # Seed prerequisite rows so FK constraints are satisfied
    conn.execute(
        "INSERT INTO files (id, relative_path, title, source_hash, indexed_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("file-1", "dummy.md", "Dummy", "abc", "2026-01-01T00:00:00"),
    )
    conn.execute(
        "INSERT INTO embedding_profiles (name, model, dimensions) VALUES (?, ?, ?)",
        ("test-profile", "test-model", 3),
    )
    conn.execute(
        "INSERT INTO chunks (id, file_id, content, content_type, chunk_index) "
        "VALUES (?, ?, ?, ?, ?)",
        ("chunk-1", "file-1", "hello", "text", 0),
    )
    conn.commit()
    yield conn, VectorIndex(conn)
    conn.close()
    db_path.unlink(missing_ok=True)


def test_upsert_and_retrieve(conn_and_index: tuple[sqlite3.Connection, VectorIndex]) -> None:
    conn, index = conn_and_index
    index.upsert("chunk-1", "test-profile", [1.0, 0.0, 0.0])
    row = conn.execute(
        "SELECT embedding FROM chunk_embeddings WHERE chunk_id = 'chunk-1'"
    ).fetchone()
    assert row is not None
    import json
    vec = json.loads(row["embedding"])
    assert vec == [1.0, 0.0, 0.0]


def test_search_returns_nearest_neighbor(
    conn_and_index: tuple[sqlite3.Connection, VectorIndex],
) -> None:
    conn, index = conn_and_index

    # Seed three chunks
    for cid, content in [("c1", "aaa"), ("c2", "bbb"), ("c3", "ccc")]:
        conn.execute(
            "INSERT INTO chunks (id, file_id, content, content_type, chunk_index) "
            "VALUES (?, ?, ?, ?, ?)",
            (cid, "file-1", content, "text", 0),
        )
    conn.commit()

    index.upsert("c1", "test-profile", [1.0, 0.0, 0.0])
    index.upsert("c2", "test-profile", [0.0, 1.0, 0.0])
    index.upsert("c3", "test-profile", [0.0, 0.0, 1.0])

    results = index.search([1.0, 0.0, 0.0], "test-profile", limit=3)

    assert len(results) == 3
    assert results[0]["chunk_id"] == "c1"
    assert results[0]["score"] == pytest.approx(1.0)


def test_search_respects_limit(
    conn_and_index: tuple[sqlite3.Connection, VectorIndex],
) -> None:
    conn, index = conn_and_index
    for i in range(5):
        cid = f"ch{i}"
        conn.execute(
            "INSERT INTO chunks (id, file_id, content, content_type, chunk_index) "
            "VALUES (?, ?, ?, ?, ?)",
            (cid, "file-1", f"text-{i}", "text", i),
        )
    conn.commit()

    for i in range(5):
        vec = [0.0] * 5
        vec[i] = 1.0
        index.upsert(f"ch{i}", "test-profile", vec)

    results = index.search([1.0, 0.0, 0.0, 0.0, 0.0], "test-profile", limit=2)
    assert len(results) == 2


def test_delete(conn_and_index: tuple[sqlite3.Connection, VectorIndex]) -> None:
    conn, index = conn_and_index
    index.upsert("chunk-1", "test-profile", [1.0, 2.0, 3.0])
    assert index.count("test-profile") == 1

    index.delete("chunk-1", "test-profile")
    assert index.count("test-profile") == 0


def test_delete_all(conn_and_index: tuple[sqlite3.Connection, VectorIndex]) -> None:
    conn, index = conn_and_index
    # Add second chunk
    conn.execute(
        "INSERT INTO chunks (id, file_id, content, content_type, chunk_index) "
        "VALUES (?, ?, ?, ?, ?)",
        ("chunk-2", "file-1", "world", "text", 1),
    )
    conn.commit()

    index.upsert("chunk-1", "test-profile", [1.0, 0.0, 0.0])
    index.upsert("chunk-2", "test-profile", [0.0, 1.0, 0.0])
    assert index.count("test-profile") == 2

    deleted = index.delete_all("test-profile")
    assert deleted == 2
    assert index.count("test-profile") == 0


def test_count(conn_and_index: tuple[sqlite3.Connection, VectorIndex]) -> None:
    conn, index = conn_and_index
    assert index.count("test-profile") == 0

    index.upsert("chunk-1", "test-profile", [1.0, 0.0, 0.0])
    assert index.count("test-profile") == 1

    # Upsert same chunk again (replace)
    index.upsert("chunk-1", "test-profile", [0.0, 1.0, 0.0])
    assert index.count("test-profile") == 1


def test_empty_search(conn_and_index: tuple[sqlite3.Connection, VectorIndex]) -> None:
    _, index = conn_and_index
    results = index.search([1.0, 0.0, 0.0], "test-profile", limit=10)
    assert results == []


def test_empty_query_vector(conn_and_index: tuple[sqlite3.Connection, VectorIndex]) -> None:
    conn, index = conn_and_index
    index.upsert("chunk-1", "test-profile", [1.0, 0.0, 0.0])
    results = index.search([0.0, 0.0, 0.0], "test-profile", limit=10)
    assert results == []


def test_profile_isolation(conn_and_index: tuple[sqlite3.Connection, VectorIndex]) -> None:
    conn, index = conn_and_index
    conn.execute(
        "INSERT INTO embedding_profiles (name, model, dimensions) VALUES (?, ?, ?)",
        ("other-profile", "other-model", 3),
    )
    conn.commit()

    index.upsert("chunk-1", "test-profile", [1.0, 0.0, 0.0])
    index.upsert("chunk-1", "other-profile", [0.0, 0.0, 1.0])

    results = index.search([1.0, 0.0, 0.0], "test-profile", limit=5)
    assert len(results) == 1
    assert results[0]["chunk_id"] == "chunk-1"

    results_other = index.search([1.0, 0.0, 0.0], "other-profile", limit=5)
    assert len(results_other) == 1
    assert results_other[0]["score"] == pytest.approx(0.0)


def test_search_scores_ordering(
    conn_and_index: tuple[sqlite3.Connection, VectorIndex],
) -> None:
    """Verify results are sorted by descending similarity score."""
    conn, index = conn_and_index
    for i in range(4):
        cid = f"v{i}"
        conn.execute(
            "INSERT INTO chunks (id, file_id, content, content_type, chunk_index) "
            "VALUES (?, ?, ?, ?, ?)",
            (cid, "file-1", f"doc-{i}", "text", i),
        )
    conn.commit()

    index.upsert("v0", "test-profile", [1.0, 0.0, 0.0])
    index.upsert("v1", "test-profile", [0.9, 0.44, 0.0])  # ~cos(26°)
    index.upsert("v2", "test-profile", [0.5, 0.87, 0.0])  # ~cos(60°)
    index.upsert("v3", "test-profile", [0.0, 0.0, 1.0])   # orthogonal

    results = index.search([1.0, 0.0, 0.0], "test-profile", limit=4)
    scores = [r["score"] for r in results]
    assert scores == sorted(scores, reverse=True)
    assert results[0]["chunk_id"] == "v0"
    assert results[0]["score"] == pytest.approx(1.0)


def test_upsert_replaces_embedding(
    conn_and_index: tuple[sqlite3.Connection, VectorIndex],
) -> None:
    """Upserting the same chunk_id+profile replaces the old vector."""
    _, index = conn_and_index
    index.upsert("chunk-1", "test-profile", [1.0, 0.0, 0.0])
    index.upsert("chunk-1", "test-profile", [0.0, 1.0, 0.0])

    results = index.search([0.0, 1.0, 0.0], "test-profile", limit=1)
    assert results[0]["chunk_id"] == "chunk-1"
    assert results[0]["score"] == pytest.approx(1.0)
