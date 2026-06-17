"""Tests for the rebuild CLI commands."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from click.testing import CliRunner

from mdrack.cli import main
from mdrack.cli.commands.rebuild import DEFAULT_BATCH_SIZE, rebuild_embeddings_in_db
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


def _setup_db(tmp_path: Path, with_chunks: bool = False) -> Path:
    store_dir = tmp_path / ".mdrack"
    store_dir.mkdir()
    db_path = store_dir / "knowledge.db"
    conn = get_connection(db_path)
    try:
        apply_migrations(conn, _MIGRATIONS_DIR)
        if with_chunks:
            _seed_chunks(conn)
    finally:
        conn.close()
    return db_path


def _seed_chunks(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO files (id, relative_path, source_hash, indexed_at) "
        "VALUES (?, ?, ?, ?)",
        ("file-001", "docs/python.md", "hash-aaa", "2024-01-01T00:00:00Z"),
    )
    conn.execute(
        "INSERT INTO sections (id, file_id, title, level, start_line, end_line) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("section-001", "file-001", "Python Intro", 1, 1, 50),
    )
    conn.execute(
        "INSERT INTO chunks (id, file_id, section_id, content, content_type, chunk_index, embedding_text) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "chunk-001",
            "file-001",
            "section-001",
            "Python is a high-level programming language.",
            "text",
            0,
            "docs/python.md :: Python Intro ||| Python is a high-level programming language.",
        ),
    )
    conn.execute(
        "INSERT INTO chunks (id, file_id, content, content_type, chunk_index, embedding_text) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            "chunk-002",
            "file-001",
            "JavaScript is a scripting language for the web.",
            "text",
            1,
            "docs/javascript.md :: JS Intro ||| JavaScript is a scripting language for the web.",
        ),
    )
    conn.commit()


def _seed_many_chunks(conn: sqlite3.Connection, count: int) -> None:
    conn.execute(
        "INSERT INTO files (id, relative_path, source_hash, indexed_at) VALUES (?, ?, ?, ?)",
        ("file-many", "docs/many.md", "hash-many", "2024-01-01T00:00:00Z"),
    )
    for index in range(count):
        conn.execute(
            (
                "INSERT INTO chunks "
                "(id, file_id, content, content_type, chunk_index, embedding_text) "
                "VALUES (?, ?, ?, ?, ?, ?)"
            ),
            (
                f"chunk-many-{index:03d}",
                "file-many",
                f"Chunk content {index}",
                "text",
                index,
                f"docs/many.md :: Section {index} ||| Chunk content {index}",
            ),
        )
    conn.commit()


class CountingProvider:
    def __init__(self, *, model_name: str = "counting-model", dimensions: int = 3) -> None:
        self.model_name = model_name
        self.dimensions = dimensions
        self.endpoint = "http://localhost:1234/v1"
        self.calls: list[int] = []

    async def embed(self, texts: list[str], profile: str = "default") -> list[list[float]]:
        del profile
        self.calls.append(len(texts))
        return [[float(len(text))] * self.dimensions for text in texts]


class FailingSecondBatchProvider(CountingProvider):
    async def embed(self, texts: list[str], profile: str = "default") -> list[list[float]]:
        del profile
        self.calls.append(len(texts))
        if len(self.calls) == 2:
            raise RuntimeError("second batch failed")
        return [[float(len(text))] * self.dimensions for text in texts]


class TestRebuildFTS:
    def test_fts_rebuild_on_empty_store(self, tmp_path: Path) -> None:
        _setup_db(tmp_path, with_chunks=False)
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["--root", str(tmp_path), "rebuild", "fts"],
        )
        assert result.exit_code == 0, f"rebuild fts failed: {result.output}"
        payload = json.loads(result.output)
        assert payload["ok"] is True
        assert payload["data"]["fts_count"] == 0
        assert payload["data"]["chunk_count"] == 0

    def test_fts_rebuild_with_chunks(self, tmp_path: Path) -> None:
        _setup_db(tmp_path, with_chunks=True)
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["--root", str(tmp_path), "rebuild", "fts"],
        )
        assert result.exit_code == 0, f"rebuild fts failed: {result.output}"
        payload = json.loads(result.output)
        assert payload["ok"] is True
        assert payload["data"]["fts_count"] == 2
        assert payload["data"]["chunk_count"] == 2

    def test_fts_rebuild_output_format(self, tmp_path: Path) -> None:
        _setup_db(tmp_path, with_chunks=True)
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["--root", str(tmp_path), "rebuild", "fts"],
        )
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert "ok" in payload
        assert "data" in payload
        assert "meta" in payload
        assert "command" in payload["meta"]
        assert "rebuild" in payload["meta"]["command"]


class TestRebuildEmbeddings:
    def test_rebuild_embeddings_returns_valid_json(self, tmp_path: Path) -> None:
        _setup_db(tmp_path, with_chunks=True)
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "--root", str(tmp_path),
                "rebuild", "embeddings",
                "--provider", "fake",
            ],
        )
        assert result.exit_code == 0, f"rebuild embeddings failed: {result.output}"
        payload = json.loads(result.output)
        assert payload["ok"] is True
        assert "data" in payload
        assert "meta" in payload

    def test_rebuild_embeddings_stores_vectors(self, tmp_path: Path) -> None:
        _setup_db(tmp_path, with_chunks=True)
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "--root", str(tmp_path),
                "rebuild", "embeddings",
                "--provider", "fake",
            ],
        )
        assert result.exit_code == 0, f"rebuild embeddings failed: {result.output}"
        payload = json.loads(result.output)
        assert payload["ok"] is True
        data = payload["data"]
        assert data["embedded_count"] == 2
        assert data["total_chunks"] == 2
        assert data["profile"] == "default"
        assert data["provider"] == "fake"

        db_path = tmp_path / ".mdrack" / "knowledge.db"
        conn = get_connection(db_path)
        try:
            rows = conn.execute("SELECT chunk_id, profile_name, embedding FROM chunk_embeddings").fetchall()
            assert len(rows) == 2
            for row in rows:
                assert row["profile_name"] == "default"
                vec = json.loads(row["embedding"])
                assert isinstance(vec, list)
                assert len(vec) > 0
        finally:
            conn.close()

    def test_rebuild_embeddings_on_empty_store(self, tmp_path: Path) -> None:
        _setup_db(tmp_path, with_chunks=False)
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "--root", str(tmp_path),
                "rebuild", "embeddings",
                "--provider", "fake",
            ],
        )
        assert result.exit_code == 0, f"rebuild embeddings failed: {result.output}"
        payload = json.loads(result.output)
        assert payload["ok"] is True
        data = payload["data"]
        assert data["embedded_count"] == 0
        assert data["total_chunks"] == 0

    def test_rebuild_embeddings_output_format(self, tmp_path: Path) -> None:
        _setup_db(tmp_path, with_chunks=True)
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "--root", str(tmp_path),
                "rebuild", "embeddings",
                "--provider", "fake",
            ],
        )
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["ok"] is True
        data = payload["data"]
        assert "embedded_count" in data
        assert "total_chunks" in data
        assert "profile" in data
        assert "provider" in data
        assert "command" in payload["meta"]
        assert "rebuild" in payload["meta"]["command"]

    def test_rebuild_embeddings_custom_profile(self, tmp_path: Path) -> None:
        _setup_db(tmp_path, with_chunks=True)
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "--root", str(tmp_path),
                "rebuild", "embeddings",
                "--provider", "fake",
                "--profile", "custom-profile",
            ],
        )
        assert result.exit_code == 0, f"rebuild embeddings failed: {result.output}"
        payload = json.loads(result.output)
        assert payload["ok"] is True
        data = payload["data"]
        assert data["profile"] == "custom-profile"

        db_path = tmp_path / ".mdrack" / "knowledge.db"
        conn = get_connection(db_path)
        try:
            rows = conn.execute(
                "SELECT profile_name FROM chunk_embeddings WHERE profile_name = 'custom-profile'"
            ).fetchall()
            assert len(rows) == 2
        finally:
            conn.close()

    def test_rebuild_embeddings_deterministic(self, tmp_path: Path) -> None:
        _setup_db(tmp_path, with_chunks=True)
        runner = CliRunner()

        result1 = runner.invoke(
            main,
            ["--root", str(tmp_path), "rebuild", "embeddings", "--provider", "fake"],
        )
        assert result1.exit_code == 0

        db_path = tmp_path / ".mdrack" / "knowledge.db"
        conn = get_connection(db_path)
        rows1 = conn.execute(
            "SELECT chunk_id, embedding FROM chunk_embeddings ORDER BY chunk_id"
        ).fetchall()
        conn.close()

        result2 = runner.invoke(
            main,
            ["--root", str(tmp_path), "rebuild", "embeddings", "--provider", "fake"],
        )
        assert result2.exit_code == 0

        conn2 = get_connection(db_path)
        rows2 = conn2.execute(
            "SELECT chunk_id, embedding FROM chunk_embeddings ORDER BY chunk_id"
        ).fetchall()
        conn2.close()

        for r1, r2 in zip(rows1, rows2):
            assert r1["chunk_id"] == r2["chunk_id"]
            vec1 = json.loads(r1["embedding"])
            vec2 = json.loads(r2["embedding"])
            assert vec1 == vec2

    def test_rebuild_embeddings_stores_lmstudio_profile_metadata(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        _setup_db(tmp_path, with_chunks=True)
        monkeypatch.setattr(
            "mdrack.embeddings.lmstudio.LMStudioProvider.embed",
            AsyncMock(return_value=[[0.1] * 1024, [0.2] * 1024]),
        )

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["--root", str(tmp_path), "rebuild", "embeddings"],
        )

        assert result.exit_code == 0, f"rebuild embeddings failed: {result.output}"

        db_path = tmp_path / ".mdrack" / "knowledge.db"
        conn = get_connection(db_path)
        try:
            row = conn.execute(
                "SELECT model, dimensions, endpoint FROM embedding_profiles WHERE name = 'default'"
            ).fetchone()
            assert row is not None
            assert row["model"] == "qwen3-embedding-0.6b"
            assert row["dimensions"] == 1024
            assert row["endpoint"] == "http://localhost:1234/v1"
        finally:
            conn.close()

    def test_rebuild_embeddings_batches_large_rebuilds(self, tmp_path: Path) -> None:
        db_path = _setup_db(tmp_path, with_chunks=False)
        conn = get_connection(db_path)
        try:
            _seed_many_chunks(conn, DEFAULT_BATCH_SIZE + 3)
        finally:
            conn.close()

        provider = CountingProvider()
        data = rebuild_embeddings_in_db(db_path, provider, "default")

        assert data["embedded_count"] == DEFAULT_BATCH_SIZE + 3
        assert provider.calls == [DEFAULT_BATCH_SIZE, 3]

        conn = get_connection(db_path)
        try:
            count = conn.execute(
                "SELECT COUNT(*) AS cnt FROM chunk_embeddings WHERE profile_name = 'default'"
            ).fetchone()["cnt"]
            assert count == DEFAULT_BATCH_SIZE + 3
        finally:
            conn.close()

    def test_rebuild_embeddings_rolls_back_vectors_and_profile_on_batch_failure(self, tmp_path: Path) -> None:
        db_path = _setup_db(tmp_path, with_chunks=False)
        conn = get_connection(db_path)
        try:
            _seed_many_chunks(conn, DEFAULT_BATCH_SIZE + 3)
        finally:
            conn.close()

        initial_provider = CountingProvider(model_name="initial-model", dimensions=3)
        rebuild_embeddings_in_db(db_path, initial_provider, "default")

        failing_provider = FailingSecondBatchProvider(model_name="failed-model", dimensions=5)
        with pytest.raises(RuntimeError, match="second batch failed"):
            rebuild_embeddings_in_db(db_path, failing_provider, "default")

        conn = get_connection(db_path)
        try:
            profile = conn.execute(
                "SELECT model, dimensions, endpoint FROM embedding_profiles WHERE name = 'default'"
            ).fetchone()
            assert profile is not None
            assert profile["model"] == "initial-model"
            assert profile["dimensions"] == 3
            assert profile["endpoint"] == "http://localhost:1234/v1"

            count = conn.execute(
                "SELECT COUNT(*) AS cnt FROM chunk_embeddings WHERE profile_name = 'default'"
            ).fetchone()["cnt"]
            assert count == DEFAULT_BATCH_SIZE + 3
        finally:
            conn.close()
