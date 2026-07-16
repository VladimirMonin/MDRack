from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from mdrack.eval.privacy import scan_privacy
from mdrack.eval.reporting import build_baseline_comparison_report
from scripts.baseline_compare import (
    HISTORICAL_REVISION,
    _git_sha,
    _implementation_identity,
    _run_checkout,
    _write_summary,
    corpus_fingerprint,
    load_baseline_queries,
)


def test_corpus_fingerprint_uses_sorted_relative_paths_and_bytes(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "z.md").write_bytes(b"last\n")
    (corpus / "a.md").write_bytes("первый\n".encode())

    digest = hashlib.sha256()
    for relative_path in ("a.md", "z.md"):
        path_bytes = relative_path.encode("utf-8")
        content = (corpus / relative_path).read_bytes()
        digest.update(len(path_bytes).to_bytes(8, "big"))
        digest.update(path_bytes)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)

    assert corpus_fingerprint(corpus) == f"sha256:{digest.hexdigest()}"


def test_public_baseline_fixture_is_bilingual_and_has_stable_expectations() -> None:
    repo = Path(__file__).resolve().parents[2]
    corpus = repo / "tests/fixtures/baseline_corpus"
    query_path = repo / "tests/retrieval_eval/baseline_queries.yaml"

    queries = load_baseline_queries(query_path)
    corpus_text = "\n".join(path.read_text(encoding="utf-8") for path in sorted(corpus.glob("*.md")))

    assert len(list(corpus.glob("*.md"))) == 3
    assert "Neighbor context" in corpus_text
    assert "Повторяемость" in corpus_text
    assert {query["mode"] for query in queries} == {"text"}
    assert all(query["expected"]["source_facts"] for query in queries)
    assert all(
        set(fact) == {"relative_path"}
        for query in queries
        for fact in query["expected"]["source_facts"]
    )


def test_current_metrics_use_stable_source_fact_denominators() -> None:
    repo = Path(__file__).resolve().parents[2]
    queries = load_baseline_queries(repo / "tests/retrieval_eval/baseline_queries.yaml")

    result = _run_checkout(
        repo,
        _git_sha(repo),
        repo / "tests/fixtures/baseline_corpus",
        queries,
        historical=False,
    )

    assert result["status"] == "available"
    assert [item["expected_fact_count"] for item in result["results"]] == [1, 1, 1]
    assert all(item["metrics"]["recall_at_k"] == 1.0 for item in result["results"])


def test_current_implementation_identity_covers_exact_phase1_bytes(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[2]
    checkout = tmp_path / "checkout"
    for relative_path in (
        "scripts/baseline_compare.py",
        "src/mdrack/eval/reporting.py",
        "tests/retrieval_eval/baseline_queries.yaml",
    ):
        source = repo / relative_path
        target = checkout / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    shutil.copytree(
        repo / "tests/fixtures/baseline_corpus",
        checkout / "tests/fixtures/baseline_corpus",
    )
    corpus = checkout / "tests/fixtures/baseline_corpus"
    queries = checkout / "tests/retrieval_eval/baseline_queries.yaml"

    first = _implementation_identity(checkout, "a" * 40, corpus, queries)
    second = _implementation_identity(checkout, "a" * 40, corpus, queries)
    assert first == second
    assert first["head_sha"] == "a" * 40
    assert first["manifest"]["entry_count"] == 6
    assert first["manifest"]["digest"].startswith("sha256:")
    assert "/home/" not in json.dumps(first)

    (checkout / "src/mdrack/eval/reporting.py").write_bytes(b"changed\n")
    changed = _implementation_identity(checkout, "a" * 40, corpus, queries)
    assert changed["manifest"]["digest"] != first["manifest"]["digest"]


def test_wrong_historical_checkout_fails_closed() -> None:
    repo = Path(__file__).resolve().parents[2]
    result = _run_checkout(
        repo,
        HISTORICAL_REVISION,
        repo / "tests/fixtures/baseline_corpus",
        load_baseline_queries(repo / "tests/retrieval_eval/baseline_queries.yaml"),
        historical=True,
    )

    assert result == {
        "status": "unavailable",
        "commit_sha": HISTORICAL_REVISION,
        "historical_baseline_unavailable": "historical_revision_mismatch",
    }


def test_comparison_report_omits_raw_queries_paths_and_database_ids() -> None:
    raw_queries = ["neighboring chunks stable source facts", "секретный запрос"]
    current = {
        "status": "available",
        "commit_sha": "a" * 40,
        "environment": {
            "python": "3.11.9",
            "os": "Linux",
            "dependencies": {"click": "8.1.8"},
        },
        "commands": ["uv run --project <checkout> python <baseline-worker>"],
        "counts": {"files": 3, "blocks": 11, "chunks": 8, "errors": 0, "tests": 3},
        "timings_ms": {"index": 12.25, "search": 1.5},
        "results": [
            {
                "query_ref": "BQ001",
                "mode": "text",
                "k": 5,
                "retrieved": [{"rank": 1, "source_ref": "sha256:1234567890abcdef"}],
                "metrics": {"recall_at_k": 1.0, "mrr": 1.0, "precision_at_k": 1.0},
                "conditions_met": True,
                "error_category": None,
            }
        ],
    }
    report = build_baseline_comparison_report(
        baseline_sha="b" * 40,
        current_sha="a" * 40,
        corpus_ref="sha256:" + "c" * 64,
        query_set_ref="sha256:" + "d" * 64,
        historical={
            "status": "unavailable",
            "commit_sha": "b" * 40,
            "historical_baseline_unavailable": "checkout_runtime_incompatible",
        },
        current=current,
    )

    encoded = json.dumps(report, ensure_ascii=False, sort_keys=True)
    assert not any(query in encoded for query in raw_queries)
    assert "/home/" not in encoded
    assert "knowledge.db" not in encoded
    assert "historical_baseline_unavailable" in encoded
    assert scan_privacy(report, forbidden_values=raw_queries).safe


def test_comparison_report_rejects_unclassified_historical_failure() -> None:
    with pytest.raises(ValueError, match="historical_baseline_unavailable"):
        build_baseline_comparison_report(
            baseline_sha="b" * 40,
            current_sha="a" * 40,
            corpus_ref="sha256:" + "c" * 64,
            query_set_ref="sha256:" + "d" * 64,
            historical={"status": "unavailable", "commit_sha": "b" * 40},
            current={"status": "available", "commit_sha": "a" * 40},
        )


def test_public_summary_contains_complete_copyable_reproduction_sequence(tmp_path: Path) -> None:
    report = {
        "revisions": {"historical": HISTORICAL_REVISION, "current": "a" * 40},
        "implementation_identity": {
            "head_sha": "a" * 40,
            "manifest": {"digest": "sha256:" + "b" * 64, "entry_count": 6},
        },
        "corpus_fingerprint": "sha256:" + "c" * 64,
        "query_set_fingerprint": "sha256:" + "d" * 64,
        "historical": {
            "status": "unavailable",
            "historical_baseline_unavailable": "checkout_missing",
        },
        "current": {
            "status": "available",
            "counts": {"files": 3, "blocks": 20, "chunks": 12, "errors": 0},
            "test_counts": {"passed": 3, "total": 3},
        },
        "comparison": {"comparable": False},
    }
    output = tmp_path / "baseline.md"

    _write_summary(output, report, "sha256:" + "e" * 64)

    summary = output.read_text(encoding="utf-8")
    assert "git worktree add --detach ../mdrack-cbd60b8 cbd60b8" in summary
    assert "--baseline-checkout ../mdrack-cbd60b8" in summary
    assert "--current-checkout ." in summary
    assert "--summary-output docs/evidence/v0.2-baseline.md" in summary
    assert "/home/" not in summary


def test_missing_historical_checkout_is_reported_without_exception_text(tmp_path: Path) -> None:
    result = _run_checkout(
        tmp_path / "missing-checkout",
        HISTORICAL_REVISION,
        tmp_path,
        [],
        historical=True,
    )

    assert result == {
        "status": "unavailable",
        "commit_sha": HISTORICAL_REVISION,
        "historical_baseline_unavailable": "checkout_missing",
    }
