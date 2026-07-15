"""Privacy-safe serialization for retrieval evaluation baselines."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from mdrack.eval.retrieval import EvalReport


def _safe_ref(value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
    return f"sha256:{digest}"


def _error_category(error: str | None) -> str | None:
    if error is None:
        return None
    lowered = error.lower()
    if "zero chunks" in lowered:
        return "zero_gold"
    if "provider" in lowered or "backend" in lowered:
        return "provider_error"
    if "search" in lowered:
        return "search_error"
    return "evaluation_error"


@dataclass(frozen=True)
class RetrievalBaselineReport:
    """Stable report contract that excludes raw queries, paths, and chunk IDs."""

    benchmark_ref: str
    corpus_ref: str
    index_ref: str
    profile_ref: str
    parser_ref: str
    chunker_ref: str
    results: list[dict[str, Any]]
    summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "report_type": "retrieval_baseline",
            "benchmark_ref": self.benchmark_ref,
            "corpus_ref": self.corpus_ref,
            "index_ref": self.index_ref,
            "profile_ref": self.profile_ref,
            "parser_ref": self.parser_ref,
            "chunker_ref": self.chunker_ref,
            "results": self.results,
            "summary": self.summary,
        }


def build_retrieval_report(
    evaluation: EvalReport,
    benchmark_ref: str,
    corpus_ref: str,
    index_ref: str,
    profile_ref: str,
    parser_ref: str,
    chunker_ref: str,
) -> RetrievalBaselineReport:
    """Project an internal evaluation result into a privacy-safe contract."""
    results = [
        {
            "query_ref": _safe_ref(result.query_id),
            "mode": result.mode,
            "k": result.k,
            "recall_at_k": result.recall_at_k,
            "mrr": result.mrr,
            "precision_at_k": result.precision_at_k,
            "ndcg_at_k": result.ndcg_at_k,
            "retrieved_count": len(result.retrieved_ids),
            "expected_count": len(result.expected_ids),
            "conditions_met": result.conditions_met,
            "error_category": _error_category(result.error),
        }
        for result in evaluation.results
    ]
    return RetrievalBaselineReport(
        benchmark_ref=benchmark_ref,
        corpus_ref=corpus_ref,
        index_ref=index_ref,
        profile_ref=profile_ref,
        parser_ref=parser_ref,
        chunker_ref=chunker_ref,
        results=results,
        summary=dict(evaluation.summary),
    )
