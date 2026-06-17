"""Tests for eval CLI reporting."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from mdrack.cli import main
from mdrack.eval.retrieval import EvalQueryResult, EvalReport
from mdrack.storage.sqlite.connection import get_connection
from mdrack.storage.sqlite.migrations import apply_migrations

MIGRATIONS_DIR = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "mdrack"
    / "storage"
    / "sqlite"
    / "migrations"
)


def _setup_db(tmp_path: Path) -> None:
    store_dir = tmp_path / ".mdrack"
    store_dir.mkdir()
    db_path = store_dir / "knowledge.db"
    conn = get_connection(db_path)
    try:
        apply_migrations(conn, MIGRATIONS_DIR)
    finally:
        conn.close()


def test_eval_cli_surfaces_query_errors_and_summary(monkeypatch, tmp_path: Path) -> None:
    _setup_db(tmp_path)
    queries_path = tmp_path / "queries.yaml"
    queries_path.write_text(
        """
queries:
  - id: Q1
    query: \"Python\"
    mode: \"semantic\"
    expected:
      content_contains: \"Python\"
    metrics:
      recall_at: 2
""".strip()
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "mdrack.cli.commands.eval.run_retrieval_eval",
        lambda *args, **kwargs: EvalReport(
            results=[
                EvalQueryResult(
                    query_id="Q1",
                    query="Python",
                    mode="semantic",
                    retrieved_ids=[],
                    expected_ids=["chunk-1"],
                    k=2,
                    recall_at_k=0.0,
                    mrr=0.0,
                    precision_at_k=0.0,
                    conditions_met=False,
                    error="provider offline",
                )
            ],
            summary={
                "queries_total": 1,
                "queries_successful": 0,
                "queries_failed": 1,
                "queries_with_zero_gold": 0,
                "avg_recall_at_k": 0.0,
                "avg_mrr": 0.0,
                "avg_precision_at_k": 0.0,
            },
        ),
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "--root",
            str(tmp_path),
            "eval",
            "retrieval",
            "--queries",
            str(queries_path),
            "--provider",
            "fake",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["data"]["results"][0]["error"] == "provider offline"
    assert payload["data"]["results"][0]["conditions_met"] is False
    assert payload["data"]["summary"]["queries_failed"] == 1
