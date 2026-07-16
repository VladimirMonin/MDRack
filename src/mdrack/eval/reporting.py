"""Privacy-safe serialization for retrieval evaluation baselines."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from mdrack.eval.retrieval import EvalReport

_CHECKOUT_STATUSES = frozenset({"available", "unavailable"})


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


def build_baseline_comparison_report(
    *,
    baseline_sha: str,
    current_sha: str,
    corpus_ref: str,
    query_set_ref: str,
    historical: dict[str, Any],
    current: dict[str, Any],
    implementation_identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the stable, privacy-safe historical/current comparison contract."""
    for label, checkout in (("historical", historical), ("current", current)):
        status = checkout.get("status")
        if status not in _CHECKOUT_STATUSES:
            raise ValueError(f"{label} checkout has an invalid status")
        if status == "unavailable" and not checkout.get("historical_baseline_unavailable"):
            if label == "historical":
                raise ValueError("historical_baseline_unavailable is required")
            raise ValueError("current unavailable reason is required")

    comparison: dict[str, Any] = {"comparable": False, "metric_deltas": {}}
    if historical["status"] == current["status"] == "available":
        historical_metrics = historical.get("summary", {})
        current_metrics = current.get("summary", {})
        metric_names = sorted(set(historical_metrics) & set(current_metrics))
        comparison = {
            "comparable": True,
            "metric_deltas": {
                name: round(float(current_metrics[name]) - float(historical_metrics[name]), 12)
                for name in metric_names
                if isinstance(historical_metrics[name], (int, float))
                and not isinstance(historical_metrics[name], bool)
                and isinstance(current_metrics[name], (int, float))
                and not isinstance(current_metrics[name], bool)
            },
        }

    report = {
        "schema_version": 1,
        "report_type": "historical_current_baseline",
        "revisions": {"historical": baseline_sha, "current": current_sha},
        "corpus_fingerprint": corpus_ref,
        "query_set_fingerprint": query_set_ref,
        "historical": historical,
        "current": current,
        "comparison": comparison,
        "privacy": {
            "absolute_paths_included": False,
            "raw_queries_included": False,
            "note_text_included": False,
            "database_ids_included": False,
            "provider_bodies_included": False,
        },
    }
    if implementation_identity is not None:
        report["implementation_identity"] = implementation_identity
    return report
