"""Frozen JSON contracts for Phase 0 reports and CLI envelopes."""

from __future__ import annotations

from mdrack.eval.reporting import build_retrieval_report
from mdrack.eval.retrieval import EvalQueryResult, EvalReport
from mdrack.output.envelope import error, success


def test_success_envelope_contract_is_frozen() -> None:
    assert success({"count": 1}, "scan") == {
        "ok": True,
        "data": {"count": 1},
        "meta": {"command": "scan"},
    }


def test_error_envelope_contract_is_frozen() -> None:
    assert error("failed", "E_TEST", "scan", {"count": 1}) == {
        "ok": False,
        "error": {"message": "failed", "code": "E_TEST", "details": {"count": 1}},
        "meta": {"command": "scan"},
    }


def test_retrieval_baseline_report_omits_queries_paths_and_chunk_ids() -> None:
    evaluation = EvalReport(
        results=[
            EvalQueryResult(
                query_id="Q001",
                query="SECRET_QUERY_SENTINEL",
                mode="text",
                retrieved_ids=["private-chunk-id"],
                expected_ids=["gold-chunk-id"],
                k=10,
                recall_at_k=1.0,
                mrr=1.0,
                precision_at_k=1.0,
                ndcg_at_k=1.0,
            )
        ],
        summary={
            "queries_total": 1,
            "queries_successful": 1,
            "queries_failed": 0,
            "queries_with_zero_gold": 0,
            "avg_recall_at_k": 1.0,
            "avg_mrr": 1.0,
            "avg_precision_at_k": 1.0,
            "avg_ndcg_at_k": 1.0,
        },
    )

    payload = build_retrieval_report(
        evaluation,
        benchmark_ref="sha256:benchmark",
        corpus_ref="sha256:corpus",
        index_ref="sha256:index",
        profile_ref="sha256:profile",
        parser_ref="sha256:parser",
        chunker_ref="sha256:chunker",
    ).to_dict()

    assert set(payload) == {
        "schema_version",
        "report_type",
        "benchmark_ref",
        "corpus_ref",
        "index_ref",
        "profile_ref",
        "parser_ref",
        "chunker_ref",
        "results",
        "summary",
    }
    assert payload["corpus_ref"] == "sha256:corpus"
    assert payload["index_ref"] == "sha256:index"
    assert payload["profile_ref"] == "sha256:profile"
    assert payload["parser_ref"] == "sha256:parser"
    assert payload["chunker_ref"] == "sha256:chunker"
    assert set(payload["results"][0]) == {
        "case_ordinal",
        "mode",
        "k",
        "recall_at_k",
        "mrr",
        "precision_at_k",
        "ndcg_at_k",
        "retrieved_count",
        "expected_count",
        "conditions_met",
        "status",
    }
    rendered = str(payload)
    assert "SECRET_QUERY_SENTINEL" not in rendered
    assert "private-chunk-id" not in rendered
    assert "gold-chunk-id" not in rendered
