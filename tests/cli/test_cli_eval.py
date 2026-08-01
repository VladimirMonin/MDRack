"""Regression coverage for canonical retrieval evaluation CLI."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from mdrack.cli import main


def test_eval_retrieval_uses_the_fixed_catalog_and_safe_result_contract(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "note.md").write_text("# Private heading\n\nneedle evaluation content\n", encoding="utf-8")
    queries = root / "queries.yaml"
    queries.write_text(
        """queries:
  - id: private-case
    query: needle
    mode: text
    expected:
      content_contains: needle evaluation content
      file_path_contains: note.md
      heading_contains: Private heading
    metrics:
      recall_at: 1
""",
        encoding="utf-8",
    )
    runner = CliRunner()
    assert runner.invoke(main, ["--root", str(root), "init"]).exit_code == 0
    indexed = runner.invoke(main, ["--root", str(root), "scan", "--provider", "fake"])
    assert indexed.exit_code == 0, indexed.output

    result = runner.invoke(main, ["--root", str(root), "eval", "retrieval", "--queries", str(queries)])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)["data"]
    assert data["query_set"] == {"kind": "file", "query_count": 1}
    assert data["results"] == [
        {
            "case_ordinal": 1,
            "mode": "text",
            "k": 1,
            "recall_at_k": 1.0,
            "mrr": 1.0,
            "precision_at_k": 1.0,
            "ndcg_at_k": 1.0,
            "retrieved_count": 1,
            "expected_count": 1,
            "conditions_met": True,
            "status": "ok",
        }
    ]
    assert data["summary"]["queries_successful"] == 1
    assert "needle evaluation content" not in result.output
    assert "Private heading" not in result.output
    assert str(root) not in result.output


def test_eval_group_is_registered_without_a_catalog_override() -> None:
    result = CliRunner().invoke(main, ["eval", "--help"])

    assert result.exit_code == 0, result.output
    assert "retrieval" in result.output
    assert "--catalog" not in result.output



def test_eval_retrieval_fails_safely_when_the_configured_catalog_is_missing(tmp_path: Path) -> None:
    queries = tmp_path / "queries.yaml"
    queries.write_text(
        """queries:
  - id: missing-catalog
    query: private query
    mode: text
    expected:
      content_contains: private content
    metrics:
      recall_at: 1
""",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        main,
        ["--root", str(tmp_path), "eval", "retrieval", "--queries", str(queries)],
    )

    assert result.exit_code == 1
    assert json.loads(result.output)["error"] == {
        "message": "Evaluation store is unavailable",
        "code": "STORAGE_ERROR",
    }
    assert "private" not in result.output
    assert str(tmp_path) not in result.output
