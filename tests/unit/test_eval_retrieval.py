"""Tests for retrieval evaluation metrics and failure reporting."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from mdrack.config.models import MDRackConfig
from mdrack.embeddings.protocol import EmbeddingError, EmbeddingHealth
from mdrack.eval.queries import EvalQuery, EvalQuerySet
from mdrack.eval.retrieval import run_retrieval_eval
from mdrack.search.hybrid import HybridSearchResult, HybridSearchResultItem
from mdrack.storage.sqlite.connection import get_connection
from mdrack.storage.sqlite.fts import upsert_fts
from mdrack.storage.sqlite.migrations import apply_migrations

MIGRATIONS_DIR = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "mdrack"
    / "storage"
    / "sqlite"
    / "migrations"
)


class FailingProvider:
    """Embedding provider stub that fails query embedding."""

    @property
    def dimensions(self) -> int:
        return 8

    async def embed(self, texts: list[str], profile: str = "default") -> list[list[float]]:
        raise EmbeddingError("provider offline")

    async def embed_query(self, text: str, profile: str = "default") -> list[float]:
        raise EmbeddingError("provider offline")

    async def health(self) -> EmbeddingHealth:
        return EmbeddingHealth(
            ok=False,
            provider="failing",
            model="test",
            dimensions=self.dimensions,
            error="provider offline",
        )


def _setup_db(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / "eval.db"
    conn = get_connection(db_path)
    apply_migrations(conn, MIGRATIONS_DIR)
    conn.execute(
        "INSERT INTO files (id, relative_path, source_hash, indexed_at) VALUES (?, ?, ?, ?)",
        ("file-1", "docs/test.md", "hash-1", "2026-01-01T00:00:00Z"),
    )
    conn.execute(
        "INSERT INTO chunks (id, file_id, content, content_type, chunk_index, heading_path) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("chunk-1", "file-1", "Python retrieval guidance", "text", 0, "Guide"),
    )
    upsert_fts(conn, "chunk-1", "Python retrieval guidance", "text", "Guide")
    conn.commit()
    return conn


def test_zero_gold_targets_are_reported_as_failures(tmp_path: Path) -> None:
    conn = _setup_db(tmp_path)
    try:
        report = run_retrieval_eval(
            conn,
            EvalQuerySet(
                queries=[
                    EvalQuery(
                        id="Q-zero-gold",
                        query="Python",
                        mode="text",
                        expected={"content_contains": "does not exist"},
                        metrics={"recall_at": 1},
                    )
                ]
            ),
            provider=FailingProvider(),
            k=5,
        )
    finally:
        conn.close()

    result = report.results[0]
    assert result.expected_ids == []
    assert result.conditions_met is False
    assert result.error == "Expected clauses matched zero chunks"
    assert result.recall_at_k == 0.0
    assert result.mrr == 0.0
    assert result.precision_at_k == 0.0
    assert result.k == 1
    assert report.summary["queries_successful"] == 0
    assert report.summary["queries_failed"] == 1
    assert report.summary["queries_with_zero_gold"] == 1


def test_semantic_provider_failure_is_exposed_in_report(tmp_path: Path) -> None:
    conn = _setup_db(tmp_path)
    try:
        report = run_retrieval_eval(
            conn,
            EvalQuerySet(
                queries=[
                    EvalQuery(
                        id="Q-semantic-fail",
                        query="Python",
                        mode="semantic",
                        expected={"content_contains": "Python retrieval"},
                        metrics={"recall_at": 2},
                    )
                ]
            ),
            provider=FailingProvider(),
            k=5,
        )
    finally:
        conn.close()

    result = report.results[0]
    assert result.retrieved_ids == []
    assert result.conditions_met is False
    assert result.error == "embedding_provider_error"
    assert report.summary["queries_failed"] == 1
    assert report.summary["queries_successful"] == 0


def test_hybrid_failure_keeps_results_but_marks_query_failed(tmp_path: Path, monkeypatch) -> None:
    async def _fake_hybrid_search(*args, **kwargs) -> HybridSearchResult:
        return HybridSearchResult(
            query="Python",
            results=[
                HybridSearchResultItem(
                    chunk_id="chunk-1",
                    combined_score=0.9,
                    text_rank=1,
                    semantic_rank=None,
                    text_score=0.9,
                    semantic_score=None,
                    content_preview="Python retrieval guidance",
                    file_relative_path="docs/test.md",
                    section_title=None,
                    heading_path="Guide",
                )
            ],
            total_count=1,
            error="semantic backend timed out",
            degraded=True,
        )

    monkeypatch.setattr("mdrack.eval.retrieval.hybrid_search", _fake_hybrid_search)

    conn = _setup_db(tmp_path)
    try:
        report = run_retrieval_eval(
            conn,
            EvalQuerySet(
                queries=[
                    EvalQuery(
                        id="Q-hybrid-fail",
                        query="Python",
                        mode="hybrid",
                        expected={"content_contains": "Python retrieval"},
                        metrics={"recall_at": 1},
                    )
                ]
            ),
            provider=FailingProvider(),
            config=MDRackConfig(),
            k=5,
        )
    finally:
        conn.close()

    result = report.results[0]
    assert result.retrieved_ids == ["chunk-1"]
    assert result.conditions_met is False
    assert result.error == "semantic backend timed out"
    assert result.recall_at_k == 1.0
    assert report.summary["queries_failed"] == 1
