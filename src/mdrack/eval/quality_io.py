"""Strict JSON input/report readers for quality v2, shared by the offline CLI.

Unknown fields, non-finite values and duplicate keys fail closed. Reports contain
only fixed enums, digests, ordinals and numbers, never caller identity strings.
"""

from __future__ import annotations

import json
import math
from copy import deepcopy
from typing import Any

from mdrack.eval.quality import QualityEvaluationError
from mdrack.eval.quality_v2 import (
    EVALUATOR_VERSION,
    REPORT_CONTRACT,
    _aggregate,
    _array,
    _enum,
    _fail,
    _integer,
    _object,
    validate_quality_input,
)

REPORT_KEYS = {
    "contract",
    "schema_version",
    "report_type",
    "evaluator_version",
    "input_ref",
    "corpus_ref",
    "judgments_ref",
    "implementation_ref",
    "cases",
    "results",
    "summary",
    "evaluation_elapsed_ms",
    "evidence_boundary",
    "search_latency_measured",
}
RESULT_KEYS = {
    "case_ordinal",
    "case_type",
    "target",
    "match",
    "judgment_policy",
    "unjudged_policy",
    "recall_basis",
    "temporal_policy",
    "positive_gold",
    "excluded_judgments",
    "ranked_count",
    "cutoffs",
}
ROW_KEYS = {
    "k",
    "returned",
    "judged_returned",
    "unjudged_returned",
    "zero_grade_returned",
    "judged_fraction",
    "relevant_hits",
    "recall",
    "mrr",
    "ndcg",
    "hit",
    "negative_returned",
    "empty",
    "temporal",
}
TEMPORAL_KEYS = {
    "gold",
    "matches",
    "hit",
    "recall",
    "interval_matches",
    "timestamp_matches",
    "mean_iou",
    "mean_start_error_ms",
    "mean_end_error_ms",
    "mean_timestamp_error_ms",
}


def _decode(text: str) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                _fail("duplicate JSON key")
            result[key] = value
        return result

    def constant(_: str) -> None:
        _fail("nonfinite JSON constant")

    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, TypeError, RecursionError):
        # Never retain a JSONDecodeError carrying a caller's document/locator.
        raise QualityEvaluationError("invalid quality JSON") from None


def loads_quality_input(text: str) -> dict[str, Any]:
    """Read only schema 2. No implicit migration of frozen v1 input."""
    return validate_quality_input(_decode(text))


def _number(value: Any, *, nullable: bool = False, maximum: float | None = None) -> None:
    if nullable and value is None:
        return
    if type(value) not in (int, float) or abs(value) > 2**53 - 1 or not math.isfinite(value) or value < 0:
        _fail("invalid report number")
    if maximum is not None and value > maximum:
        _fail("report number outside range")


def _reference(value: Any) -> None:
    from mdrack.eval.quality_v2 import _SHA

    if type(value) is not str or _SHA.fullmatch(value) is None:
        _fail("invalid report reference")


def _validate_temporal(row: dict[str, Any], result: dict[str, Any]) -> None:
    temporal = _object(row["temporal"], TEMPORAL_KEYS)
    for name in ("gold", "matches", "hit", "interval_matches", "timestamp_matches"):
        _integer(temporal[name])
    count = temporal["matches"]
    if (
        temporal["gold"] != result["positive_gold"]
        or count > temporal["gold"]
        or count != temporal["interval_matches"] + temporal["timestamp_matches"]
        or temporal["hit"] != int(count > 0)
        or count > row["returned"]
    ):
        _fail("inconsistent temporal counts")
    if temporal["recall"] != (count / temporal["gold"] if temporal["gold"] else None):
        _fail("inconsistent temporal recall")
    _number(temporal["recall"], nullable=True, maximum=1)
    for name in ("mean_iou", "mean_start_error_ms", "mean_end_error_ms", "mean_timestamp_error_ms"):
        count_key = "timestamp_matches" if name == "mean_timestamp_error_ms" else "interval_matches"
        if (temporal[name] is None) != (temporal[count_key] == 0):
            _fail("missing temporal samples require null error")
        _number(temporal[name], nullable=True, maximum=1 if name == "mean_iou" else None)
    if temporal["mean_iou"] is not None and temporal["mean_iou"] < result["temporal_policy"]["interval_iou"]:
        _fail("matched IoU below policy")
    if (
        temporal["mean_timestamp_error_ms"] is not None
        and temporal["mean_timestamp_error_ms"] > result["temporal_policy"]["timestamp_tolerance_ms"]
    ):
        _fail("timestamp error exceeds policy")
    if result["case_type"] == "positive" and count != row["relevant_hits"]:
        _fail("temporal hits disagree with relevance")


def _validate_row(row: Any, result: dict[str, Any]) -> None:
    _object(row, ROW_KEYS)
    for name in ("k", "returned", "judged_returned", "unjudged_returned", "zero_grade_returned"):
        _integer(row[name], 1 if name == "k" else 0)
    if (
        row["returned"] != min(row["k"], result["ranked_count"])
        or row["judged_returned"] + row["unjudged_returned"] != row["returned"]
        or row["zero_grade_returned"] > row["judged_returned"]
    ):
        _fail("inconsistent coverage counts")
    expected_fraction = row["judged_returned"] / row["returned"] if row["returned"] else None
    _number(row["judged_fraction"], nullable=True, maximum=1)
    if row["judged_fraction"] != expected_fraction:
        _fail("inconsistent judged fraction")
    if result["judgment_policy"] == "complete" and row["unjudged_returned"]:
        _fail("complete report contains unjudged results")
    positive_names = ("relevant_hits", "recall", "mrr", "ndcg", "hit")
    if result["case_type"] == "positive":
        _integer(row["relevant_hits"])
        hits = row["relevant_hits"]
        if hits > min(result["positive_gold"], row["judged_returned"] - row["zero_grade_returned"]):
            _fail("inconsistent relevant hits")
        for name in ("recall", "mrr", "ndcg", "hit"):
            _number(row[name], maximum=1)
        _integer(row["hit"])
        if row["recall"] != hits / result["positive_gold"] or row["hit"] != int(hits > 0):
            _fail("inconsistent positive metrics")
        if (hits == 0) != (row["mrr"] == 0) or (hits == 0) != (row["ndcg"] == 0):
            _fail("inconsistent gain or reciprocal rank")
        if hits:
            # All hits need positions from the first relevant rank through returned.
            latest_first_rank = min(row["k"], row["returned"] - hits + 1)
            if row["mrr"] < 1.0 / latest_first_rank:
                _fail("infeasible reciprocal rank")
            first_rank = round(1.0 / row["mrr"])
            if not 1 <= first_rank <= latest_first_rank or row["mrr"] != 1.0 / first_rank:
                _fail("infeasible reciprocal rank")
        if row["negative_returned"] is not None or row["empty"] is not None:
            _fail("positive case has negative metrics")
    else:
        if any(row[name] is not None for name in positive_names):
            _fail("negative case has positive metrics")
        _integer(row["negative_returned"])
        _integer(row["empty"])
        if row["negative_returned"] != row["returned"] or row["empty"] != int(row["returned"] == 0):
            _fail("inconsistent negative metrics")
    if result["match"] == "temporal":
        _validate_temporal(row, result)
    elif row["temporal"] is not None:
        _fail("identity case has temporal metrics")


def validate_quality_report(document: object) -> dict[str, Any]:
    """Validate v2 report fields/types/coverage/aggregation before read or write."""
    value = _object(document, REPORT_KEYS)
    if (
        value["contract"] != REPORT_CONTRACT
        or type(value["schema_version"]) is not int
        or value["schema_version"] != 2
        or value["report_type"] != "graded_retrieval_quality"
        or value["evaluator_version"] != EVALUATOR_VERSION
        or value["evidence_boundary"] != "offline/already-ranked"
        or value["search_latency_measured"] is not False
    ):
        _fail("unsupported quality report")
    for name in ("input_ref", "corpus_ref", "judgments_ref", "implementation_ref"):
        _reference(value[name])
    _number(value["evaluation_elapsed_ms"])
    _integer(value["cases"], 1)
    results = _array(value["results"])
    if value["cases"] != len(results):
        _fail("inconsistent case count")
    for ordinal, result in enumerate(results, 1):
        _object(result, RESULT_KEYS)
        _integer(result["case_ordinal"], 1)
        if result["case_ordinal"] != ordinal:
            _fail("invalid case ordinal")
        _enum(result["case_type"], {"positive", "negative"})
        _enum(result["target"], {"unit", "resource"})
        _enum(result["match"], {"identity", "temporal"})
        _enum(result["judgment_policy"], {"complete", "pooled"})
        if result["unjudged_policy"] != (
            "error" if result["judgment_policy"] == "complete" else "nonrelevant"
        ) or result["recall_basis"] != ("complete_gold" if result["judgment_policy"] == "complete" else "judged_pool"):
            _fail("inconsistent report judgment policy")
        for name in ("positive_gold", "excluded_judgments", "ranked_count"):
            _integer(result[name])
        if (result["case_type"] == "positive") != bool(result["positive_gold"]):
            _fail("inconsistent case type")
        if result["match"] == "temporal":
            if result["target"] != "unit":
                _fail("temporal report requires unit target")
            policy = _object(result["temporal_policy"], {"interval_iou", "timestamp_tolerance_ms"})
            _number(policy["interval_iou"], maximum=1)
            if not policy["interval_iou"]:
                _fail("invalid temporal policy")
            _integer(policy["timestamp_tolerance_ms"])
        elif result["temporal_policy"] is not None:
            _fail("identity report has temporal policy")
        rows = _array(result["cutoffs"])
        if not rows:
            _fail("empty report cutoffs")
        for row in rows:
            _validate_row(row, result)
        if [r["k"] for r in rows] != sorted({r["k"] for r in rows}):
            _fail("invalid report cutoffs")
    # Canonical JSON comparison is type-sensitive (unlike True == 1).
    expected = json.dumps(_aggregate(results), sort_keys=True, allow_nan=False)
    try:
        actual = json.dumps(value["summary"], sort_keys=True, allow_nan=False)
    except (ValueError, TypeError):
        _fail("invalid quality summary")
    if actual != expected:
        _fail("quality summary disagrees with results")
    return deepcopy(value)


def dumps_quality_report(report: object) -> str:
    """Serialize only a validated, closed, privacy-safe v2 report."""
    return json.dumps(validate_quality_report(report), sort_keys=True, ensure_ascii=True, allow_nan=False) + "\n"


def loads_quality_report(text: str) -> dict[str, Any]:
    """Read v2 reports; v1 reports keep their original independent reader."""
    return validate_quality_report(_decode(text))


__all__ = ["loads_quality_input", "loads_quality_report", "dumps_quality_report", "validate_quality_report"]
