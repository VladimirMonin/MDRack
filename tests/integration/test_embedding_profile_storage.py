"""SQLite guards against incompatible active embedding vectors."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from mdrack.adapters.sqlite.index_storage import IncompatibleEmbeddingProfileError, SQLiteIndexStorage
from mdrack.domain.indexing import PreparedFile, StoredChunk, StoredSection
from mdrack.domain.profiles import EmbeddingProfile
from mdrack.storage.sqlite.connection import get_connection
from mdrack.storage.sqlite.migrations import apply_migrations, get_migrations_dir
from mdrack.storage.sqlite.vector import VectorIndex


def _profile(model_key: str, dimensions: int) -> EmbeddingProfile:
    return EmbeddingProfile(
        name="active",
        provider="fake",
        runtime="offline-test",
        model_key=model_key,
        model_family="fake-family",
        quantization="none",
        output_dimensions=dimensions,
        query_instruction="test-query",
        normalization_mode="l2",
        endpoint_family="offline",
    )


def _prepared(profile: EmbeddingProfile, record_id: str = "file") -> PreparedFile:
    section = StoredSection("section", "section-logical", "Title", ("Title",), 1, 1, 2, None)
    chunk = StoredChunk(
        "chunk",
        "chunk-logical",
        "section",
        "safe",
        "text",
        0,
        ("Title",),
        None,
        None,
        "safe",
        "hash",
        1,
        2,
        "block-logical",
    )
    return PreparedFile(
        record_id=record_id,
        logical_id="file-logical",
        root_id="default",
        relative_path="safe.md",
        title="Safe",
        source_hash="hash",
        indexed_at="2026-01-01T00:00:00Z",
        parser_name="test",
        parser_version="1",
        chunk_strategy_name="test",
        chunk_strategy_version="1",
        index_run_id="run",
        sections=(section,),
        chunks=(chunk,),
        vectors=(tuple(0.1 for _ in range(profile.output_dimensions)),),
        embedding_profile=profile,
    )


def _storage(tmp_path: Path) -> tuple[SQLiteIndexStorage, sqlite3.Connection]:
    conn = get_connection(tmp_path / "knowledge.db")
    apply_migrations(conn, get_migrations_dir())
    conn.execute(
        "INSERT INTO index_runs (id, started_at, status) VALUES ('run', '2026-01-01T00:00:00Z', 'running')"
    )
    conn.commit()
    return SQLiteIndexStorage(conn), conn


def test_profile_fingerprint_is_persisted_with_vectors(tmp_path: Path) -> None:
    storage, conn = _storage(tmp_path)
    profile = _profile("model-a", 4)

    storage.replace_file(_prepared(profile))

    stored_profile = conn.execute("SELECT fingerprint FROM embedding_profiles WHERE name = 'active'").fetchone()
    stored_vector = conn.execute("SELECT profile_fingerprint FROM chunk_embeddings").fetchone()
    assert stored_profile["fingerprint"] == profile.fingerprint
    assert stored_vector["profile_fingerprint"] == profile.fingerprint


def test_same_profile_name_cannot_overwrite_incompatible_active_vectors(tmp_path: Path) -> None:
    storage, conn = _storage(tmp_path)
    storage.replace_file(_prepared(_profile("model-a", 4)))

    with pytest.raises(IncompatibleEmbeddingProfileError):
        storage.replace_file(_prepared(_profile("model-b", 8)))

    row = conn.execute(
        "SELECT model_key, dimensions, fingerprint FROM embedding_profiles WHERE name = 'active'"
    ).fetchone()
    assert row["model_key"] == "model-a"
    assert row["dimensions"] == 4
    assert conn.execute("SELECT COUNT(*) FROM chunk_embeddings").fetchone()[0] == 1


def test_vector_search_rejects_wrong_fingerprint_and_dimensions(tmp_path: Path) -> None:
    storage, conn = _storage(tmp_path)
    profile = _profile("model-a", 4)
    storage.replace_file(_prepared(profile))
    index = VectorIndex(conn)

    with pytest.raises(IncompatibleEmbeddingProfileError):
        index.search([0.1] * 4, "active", profile_fingerprint=_profile("model-b", 4).fingerprint)
    with pytest.raises(ValueError, match="dimension"):
        index.search([0.1] * 3, "active", profile_fingerprint=profile.fingerprint)
