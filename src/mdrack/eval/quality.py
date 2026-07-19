"""Provider-free graded retrieval quality evaluation and safe reports.

This module evaluates already-ranked public units. It deliberately does not call
embedding providers or open source locators, so reports are offline evidence only.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any


class QualityEvaluationError(ValueError):
    """A fail-closed evaluation input error with no private payload in the message."""


@dataclass(frozen=True)
class QualityUnit:
    """A ranked unit's safe identity and optional temporal coordinates."""

    unit_id: str
    resource_id: str
    start_ms: int | None = None
    end_ms: int | None = None
    timestamp_ms: int | None = None


@dataclass(frozen=True)
class QualityJudgment:
    """A graded judgment; IDs are opaque fixture identities."""

    resource_id: str
    grade: int
    unit_id: str | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    timestamp_ms: int | None = None


@dataclass(frozen=True)
class QualityCase:
    """A query case without retaining query text in the output report."""

    case_kind: str
    cutoffs: tuple[int, ...]
    mrr_cutoff: int
    ndcg_cutoff: int
    judgments: tuple[QualityJudgment, ...]
    slice_tags: tuple[str, ...] = ()
    case_id: str = ""
    query_text: str = ""


Ranker = Callable[[QualityCase], Sequence[QualityUnit]]


def _dcg(values: Iterable[float]) -> float:
    total = 0.0
    for rank, value in enumerate(values):
        total += (2.0**max(value, 0.0) - 1.0) / math.log2(rank + 2)
    return total


def _interval_iou(left: tuple[int, int], right: tuple[int, int]) -> float:
    intersection = max(0, min(left[1], right[1]) - max(left[0], right[0]))
    union = max(left[1], right[1]) - min(left[0], right[0])
    return float(intersection / union) if union else 0.0


def _temporal_metrics(
    ranked: Sequence[QualityUnit],
    judgments: Sequence[QualityJudgment],
    k: int,
) -> dict[str, float | int]:
    temporal = [item for item in judgments if item.start_ms is not None or item.timestamp_ms is not None]
    if not temporal:
        return {
            "temporal_applicable": 0,
            "timestamp_hit_at_k": 0,
            "interval_hit_at_k": 0,
            "best_iou": 0.0,
            "start_error_ms": 0,
            "end_error_ms": 0,
        }
    top = ranked[:k]
    best_iou = 0.0
    best_start = 0
    best_end = 0
    timestamp_hit = False
    interval_hit = False
    for unit in top:
        for judgment in temporal:
            if judgment.timestamp_ms is not None and unit.timestamp_ms is not None:
                distance = abs(unit.timestamp_ms - judgment.timestamp_ms)
                if distance == 0:
                    timestamp_hit = True
            elif (
                judgment.start_ms is not None
                and judgment.end_ms is not None
                and unit.start_ms is not None
                and unit.end_ms is not None
            ):
                iou = _interval_iou((unit.start_ms, unit.end_ms), (judgment.start_ms, judgment.end_ms))
                if iou > best_iou:
                    best_iou = iou
                    interval_hit = iou > 0.0
                    best_start = abs(unit.start_ms - judgment.start_ms)
                    best_end = abs(unit.end_ms - judgment.end_ms)
                elif iou > 0.0:
                    interval_hit = True
    return {
        "temporal_applicable": 1,
        "timestamp_hit_at_k": int(timestamp_hit),
        "interval_hit_at_k": int(interval_hit),
        "best_iou": round(best_iou, 12),
        "start_error_ms": best_start,
        "end_error_ms": best_end,
    }


def evaluate_quality(
    cases: Sequence[QualityCase],
    ranker: Ranker,
    *,
    corpus_ref: str,
    implementation_ref: str,
) -> dict[str, Any]:
    """Evaluate ranked units and return a privacy-safe aggregate report.

    Zero-gold cases, duplicate case judgments, and duplicate ranked IDs fail
    closed. The report contains ordinals, counts, metrics, slices, and digests;
    it never contains query text, source text, paths, or unit IDs.
    """
    if not cases:
        raise QualityEvaluationError("evaluation case set is empty")
    started = time.perf_counter_ns()
    records: list[dict[str, Any]] = []
    slice_values: dict[str, list[dict[str, float | int]]] = defaultdict(list)
    temporal_metric_names = {
        "timestamp_hit_at_k",
        "interval_hit_at_k",
        "best_iou",
        "start_error_ms",
        "end_error_ms",
    }
    for ordinal, case in enumerate(cases, start=1):
        if not case.judgments or not any(item.grade > 0 for item in case.judgments):
            raise QualityEvaluationError("zero-gold case")
        judgment_keys = [
            (item.resource_id, item.unit_id, item.start_ms, item.end_ms, item.timestamp_ms)
            for item in case.judgments
        ]
        if len(judgment_keys) != len(set(judgment_keys)):
            raise QualityEvaluationError("duplicate judgment")
        ranked = tuple(ranker(case))
        ids = [item.unit_id for item in ranked]
        if len(ids) != len(set(ids)):
            raise QualityEvaluationError("duplicate ranked ID")
        resource_target = all(item.unit_id is None for item in case.judgments)
        grades = {
            (item.resource_id if resource_target else item.unit_id): float(item.grade)
            for item in case.judgments
            if item.unit_id is not None or resource_target
        }
        positive = set(grades)
        ranked_keys = [item.resource_id if resource_target else item.unit_id for item in ranked]
        metrics: dict[str, float | int] = {}
        for cutoff in case.cutoffs:
            if cutoff < 1:
                raise QualityEvaluationError("invalid cutoff")
            metrics[f"recall_at_{cutoff}"] = round(len(positive.intersection(ranked_keys[:cutoff])) / len(positive), 12)
        first_rank = next((rank for rank, item in enumerate(ranked_keys, 1) if item in positive), None)
        metrics["mrr"] = round(1.0 / first_rank if first_rank and first_rank <= case.mrr_cutoff else 0.0, 12)
        actual = [grades.get(item, 0.0) for item in ranked_keys[: case.ndcg_cutoff]]
        ideal = sorted(grades.values(), reverse=True)[: case.ndcg_cutoff]
        metrics["ndcg"] = round(_dcg(actual) / _dcg(ideal), 12) if _dcg(ideal) else 0.0
        metrics.update(_temporal_metrics(ranked, case.judgments, max(case.cutoffs)))
        records.append({"case_ordinal": ordinal, "case_kind": case.case_kind, **metrics, "ranked_count": len(ids)})
        for tag in case.slice_tags:
            if ":" in tag:
                key, value = tag.split(":", 1)
                slice_values[f"{key}={value}"].append(metrics)
    metric_names = [
        key
        for key in records[0]
        if key not in {"case_ordinal", "case_kind", "ranked_count", "temporal_applicable"}
    ]

    def aggregate(rows: Sequence[dict[str, float | int]]) -> dict[str, float | int]:
        temporal_rows = [row for row in rows if row["temporal_applicable"]]
        return {
            "cases": len(rows),
            "temporal_cases": len(temporal_rows),
            **{
                name: round(
                    sum(
                        float(row[name])
                        for row in (temporal_rows if name in temporal_metric_names else rows)
                    )
                    / len(temporal_rows if name in temporal_metric_names else rows),
                    12,
                )
                if (temporal_rows if name in temporal_metric_names else rows)
                else 0.0
                for name in metric_names
            },
        }

    summary = aggregate(records)
    summary.pop("cases")
    by_kind: dict[str, dict[str, float | int]] = {}
    for kind in sorted({row["case_kind"] for row in records}):
        rows = [row for row in records if row["case_kind"] == kind]
        by_kind[kind] = aggregate(rows)
    by_slice = {
        key: aggregate(rows)
        for key, rows in sorted(slice_values.items())
    }
    elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
    return {
        "schema_version": 1,
        "report_type": "graded_retrieval_quality",
        "corpus_ref": corpus_ref,
        "implementation_ref": implementation_ref,
        "summary": {
            "cases": len(records),
            **summary,
            "duplicate_rate": 0.0,
            "evaluation_latency_ms": round(elapsed_ms, 6),
        },
        "by_case_kind": by_kind,
        "by_slice": by_slice,
        "results": records,
        "privacy": {
            "raw_queries_included": False,
            "raw_content_included": False,
            "paths_included": False,
            "unit_ids_included": False,
        },
    }


def fingerprint(value: object) -> str:
    """Return a reproducible SHA-256 reference for JSON-compatible config."""
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


__all__ = ["QualityCase", "QualityEvaluationError", "QualityJudgment", "QualityUnit", "evaluate_quality", "fingerprint"]
