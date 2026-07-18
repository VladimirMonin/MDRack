"""Tests for CLI semantic search with fake embedding provider."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock

from click.testing import CliRunner

from mdrack.cli import main
from mdrack.config.models import MDRackConfig
from mdrack.domain.indexing import SourceLocator
from mdrack.domain.retrieval import RetrievalItem, RetrievalResult
from mdrack.embeddings.fake import FakeEmbeddingProvider
from mdrack.embeddings.runtime import embedding_profile_from_config
from mdrack.storage.sqlite.connection import get_connection
from mdrack.storage.sqlite.migrations import apply_migrations

_MIGRATIONS_DIR = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "mdrack"
    / "storage"
    / "sqlite"
    / "migrations"
)


def _embed_text(text: str, dims: int) -> list[float]:
    provider = FakeEmbeddingProvider(dimensions=dims, provider_name="test")
    return provider._text_to_vector(text)


def _seed_semantic_data(conn: sqlite3.Connection) -> None:
    profile = embedding_profile_from_config(
        MDRackConfig(),
        FakeEmbeddingProvider(dimensions=1024),
        "default",
    )
    conn.execute(
        "INSERT INTO files (id, relative_path, source_hash, indexed_at) "
        "VALUES (?, ?, ?, ?)",
        ("file-001", "docs/python.md", "hash-aaa", "2024-01-01T00:00:00Z"),
    )
    conn.execute(
        "INSERT INTO files (id, relative_path, source_hash, indexed_at) "
        "VALUES (?, ?, ?, ?)",
        ("file-002", "docs/javascript.md", "hash-bbb", "2024-01-01T00:00:00Z"),
    )
    conn.execute(
        "INSERT INTO sections (id, file_id, title, level, start_line, end_line) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("section-001", "file-001", "Python Intro", 1, 1, 50),
    )
    conn.execute(
        "INSERT INTO chunks (id, file_id, section_id, content, content_type, chunk_index) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            "chunk-001",
            "file-001",
            "section-001",
            "Python is a high-level programming language.",
            "text",
            0,
        ),
    )
    conn.execute(
        "INSERT INTO chunks (id, file_id, content, content_type, chunk_index) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            "chunk-002",
            "file-002",
            "JavaScript is a scripting language for the web.",
            "text",
            0,
        ),
    )
    conn.execute(
        "INSERT INTO embedding_profiles (name, model, dimensions, endpoint, fingerprint) "
        "VALUES (?, ?, ?, ?, ?)",
        ("default", "fake-hash-v1", 1024, "", profile.fingerprint),
    )
    # Seed embeddings with vectors derived from the same query text
    # so the fake provider finds matching results.
    vector_python = _embed_text("Python programming language", 1024)
    conn.execute(
        "INSERT INTO chunk_embeddings "
        "(chunk_id, profile_name, embedding, embedded_at, profile_fingerprint) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            "chunk-001",
            "default",
            json.dumps(vector_python).encode("utf-8"),
            "2024-01-01T00:00:00Z",
            profile.fingerprint,
        ),
    )
    vector_js = _embed_text("JavaScript web scripting", 1024)
    conn.execute(
        "INSERT INTO chunk_embeddings "
        "(chunk_id, profile_name, embedding, embedded_at, profile_fingerprint) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            "chunk-002",
            "default",
            json.dumps(vector_js).encode("utf-8"),
            "2024-01-01T00:00:00Z",
            profile.fingerprint,
        ),
    )
    conn.commit()


def _setup_db(tmp_path: Path, with_data: bool = True) -> Path:
    store_dir = tmp_path / ".mdrack"
    store_dir.mkdir()
    db_path = store_dir / "knowledge.db"
    conn = get_connection(db_path)
    try:
        apply_migrations(conn, _MIGRATIONS_DIR)
        if with_data:
            _seed_semantic_data(conn)
    finally:
        conn.close()
    return db_path


def test_hybrid_zero_semantic_weight_does_not_create_provider(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _setup_db(tmp_path)
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "[search]\ntext_weight = 1.0\nsemantic_weight = 0.0\n",
        encoding="utf-8",
    )

    def forbidden_provider(*args, **kwargs):
        del args, kwargs
        raise AssertionError("semantic provider must not be created")

    monkeypatch.setattr(
        "mdrack.cli.commands.search.create_embedding_provider",
        forbidden_provider,
    )
    result = CliRunner().invoke(
        main,
        [
            "--root",
            str(tmp_path),
            "--config-file",
            str(config_path),
            "search",
            "Python",
            "--mode",
            "hybrid",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["ok"] is True


def test_semantic_search_returns_valid_json(tmp_path: Path) -> None:
    _setup_db(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "--root", str(tmp_path),
            "search", "Python",
            "--mode", "semantic",
            "--provider", "fake",
        ],
    )
    assert result.exit_code == 0, f"search failed: {result.output}"
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert "data" in payload
    assert "meta" in payload
    assert payload["data"]["mode"] == "semantic"
    assert len(payload["data"]["results"]) > 0


def test_semantic_search_output_format(tmp_path: Path) -> None:
    _setup_db(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "--root", str(tmp_path),
            "search", "Python",
            "--mode", "semantic",
            "--provider", "fake",
        ],
    )
    assert result.exit_code == 0, f"search failed: {result.output}"
    payload = json.loads(result.output)
    assert payload["ok"] is True
    data = payload["data"]
    assert "query" in data
    assert data["query"] == "Python"
    assert "mode" in data
    assert "results" in data
    assert "total_count" in data
    for item in data["results"]:
        assert "chunk_id" in item
        assert "score" in item
        assert "content_preview" in item
        assert "file" in item


def test_semantic_search_top_result_is_relevant(tmp_path: Path) -> None:
    _setup_db(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "--root", str(tmp_path),
            "search", "Python programming language",
            "--mode", "semantic",
            "--provider", "fake",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["ok"] is True
    results = payload["data"]["results"]
    assert len(results) > 0
    top_score = results[0]["score"]
    # When query text matches the embedding source text, cosine similarity = 1.0
    assert top_score > 0.99, f"Expected near-1.0 score, got {top_score}"


def test_semantic_search_with_no_embeddings(tmp_path: Path) -> None:
    _setup_db(tmp_path, with_data=False)
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "--root", str(tmp_path),
            "search", "Python",
            "--mode", "semantic",
            "--provider", "fake",
        ],
    )
    assert result.exit_code == 0, f"search failed: {result.output}"
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert len(payload["data"]["results"]) == 0
    assert payload["data"]["total_count"] == 0


def test_semantic_search_no_db(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "--root", str(tmp_path),
            "search", "Python",
            "--mode", "semantic",
            "--provider", "fake",
        ],
    )
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert "not found" in payload["error"]["message"].lower()


def test_hybrid_search_reports_degraded_mode(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _setup_db(tmp_path)
    locator = SourceLocator("root", "docs/python.md", 1, 2, (), "block", "chunk-001")
    monkeypatch.setattr(
        "mdrack.cli.commands.search.RetrievalService.search_hybrid",
        AsyncMock(
            return_value=RetrievalResult(
                query="Python",
                mode="hybrid",
                results=(
                    RetrievalItem(
                        logical_id="chunk-001",
                        score=0.9,
                        source_locator=locator,
                        text_rank=1,
                        semantic_rank=None,
                        text_score=0.9,
                        semantic_score=None,
                        content_preview="Python is a high-level programming language.",
                    ),
                ),
                total_count=1,
                degraded=True,
                degraded_reason="embedding_provider_error",
            )
        ),
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "--root", str(tmp_path),
            "search", "Python",
            "--mode", "hybrid",
            "--provider", "fake",
        ],
    )

    assert result.exit_code == 0, f"search failed: {result.output}"
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["data"]["degraded"] is True
    assert payload["data"]["degraded_reason"] == "embedding_provider_error"
