"""Version 2 evaluator: explicit target, scope and judged-pool semantics.

Only already-ranked identities are evaluated. No provider, source locator, query
text or content is consumed. Version 1 remains an independent frozen contract.
"""

from __future__ import annotations

import math
import re
import time
from copy import deepcopy
from typing import Any, NoReturn, cast

from mdrack.eval.quality import QualityEvaluationError, fingerprint

EVALUATOR_VERSION = "2.0.0"
INPUT_CONTRACT = "mdrack.quality-input"
REPORT_CONTRACT = "mdrack.quality-report"
_SHA = re.compile(r"sha256:[0-9a-f]{64}")
_ITEM_KEYS = {"resource_id", "source_revision", "unit_id", "intervals", "timestamp_ms"}
_CASE_KEYS = {
    "case_id",
    "case_type",
    "target",
    "match",
    "cutoffs",
    "scope",
    "judgment_policy",
    "unjudged_policy",
    "temporal_policy",
    "judgments",
    "ranked",
}


def _fail(message: str) -> NoReturn:
    raise QualityEvaluationError(message)


def _object(value: Any, keys: set[str]) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        _fail("invalid object fields")
    return cast(dict[str, Any], value)


def _array(value: Any) -> list[Any]:
    if type(value) is not list:
        _fail("expected array")
    return value


def _text(value: Any) -> str:
    if type(value) is not str or not value.strip():
        _fail("expected nonempty identity")
    return value


def _integer(value: Any, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum or value > 2**53 - 1:
        _fail("invalid integer")
    return value


def _enum(value: Any, choices: set[str]) -> None:
    if type(value) is not str or value not in choices:
        _fail("invalid enum")


def _unique(values: list[Any], message: str) -> None:
    if len(values) != len(set(values)):
        _fail(message)


def _owner(item: dict[str, Any]) -> tuple[str, str]:
    return item["resource_id"], item["source_revision"]


def _key(item: dict[str, Any], target: str) -> tuple[Any, ...]:
    return (*_owner(item), item["unit_id"] if target == "unit" else None)


def _validate_item(item: Any, case: dict[str, Any], *, judgment: bool) -> dict[str, Any]:
    keys = _ITEM_KEYS | ({"judgment_id", "grade"} if judgment else set())
    item = _object(item, keys)
    _text(item["resource_id"])
    _text(item["source_revision"])
    if item["unit_id"] is not None:
        _text(item["unit_id"])
    intervals = _array(item["intervals"])
    previous_end = -1
    for interval in intervals:
        if len(_array(interval)) != 2:
            _fail("invalid interval")
        start, end = (_integer(v) for v in interval)
        if start >= end or start <= previous_end:
            _fail("intervals must be sorted separated nonempty half-open ranges")
        previous_end = end
    if item["timestamp_ms"] is not None:
        _integer(item["timestamp_ms"])
    if judgment:
        _text(item["judgment_id"])
        if _integer(item["grade"]) > 3:
            _fail("grade outside 0..3")
    if case["match"] == "identity":
        if intervals or item["timestamp_ms"] is not None:
            _fail("identity target cannot carry temporal coordinates")
        if case["target"] == "unit" and item["unit_id"] is None:
            _fail("unit target requires unit identity")
        if judgment and case["target"] == "resource" and item["unit_id"] is not None:
            _fail("resource judgment cannot carry unit identity")
    else:
        if bool(intervals) == (item["timestamp_ms"] is not None):
            _fail("temporal item requires exactly one coordinate kind")
        if judgment and item["unit_id"] is not None:
            _fail("temporal gold is recipe-independent")
        if not judgment and item["unit_id"] is None:
            _fail("ranked temporal unit requires identity")
    return cast(dict[str, Any], item)


def validate_quality_input(document: object) -> dict[str, Any]:
    """Validate closed v2 input, returning an independent normalized JSON value."""
    value = _object(
        document,
        {
            "contract",
            "schema_version",
            "corpus_ref",
            "judgments_ref",
            "implementation_ref",
            "cases",
        },
    )
    if value["contract"] != INPUT_CONTRACT or type(value["schema_version"]) is not int or value["schema_version"] != 2:
        _fail("unsupported evaluation schema")
    for key in ("corpus_ref", "judgments_ref", "implementation_ref"):
        if type(value[key]) is not str or _SHA.fullmatch(value[key]) is None:
            _fail("expected sha256 reference")
    cases = _array(value["cases"])
    if not cases:
        _fail("empty case set")
    case_ids = []
    for case in cases:
        _object(case, _CASE_KEYS)
        case_ids.append(_text(case["case_id"]))
        _enum(case["case_type"], {"positive", "negative"})
        _enum(case["target"], {"unit", "resource"})
        _enum(case["match"], {"identity", "temporal"})
        _enum(case["judgment_policy"], {"complete", "pooled"})
        required_unjudged = "error" if case["judgment_policy"] == "complete" else "nonrelevant"
        if case["unjudged_policy"] != required_unjudged:
            _fail("judgment and unjudged policies disagree")
        cutoffs = _array(case["cutoffs"])
        if not cutoffs:
            _fail("empty cutoffs")
        for k in cutoffs:
            _integer(k, 1)
        if sorted(set(cutoffs)) != cutoffs:
            _fail("cutoffs must be increasing unique positive integers")
        scope = _array(case["scope"])
        if not scope:
            _fail("empty scope")
        for owner in scope:
            _object(owner, {"resource_id", "source_revision"})
            _text(owner["resource_id"])
            _text(owner["source_revision"])
        owners = [_owner(owner) for owner in scope]
        _unique(owners, "duplicate scope identity")
        if case["target"] == "resource":
            _unique([owner[0] for owner in owners], "resource scope must select one revision")
        if case["match"] == "temporal":
            if case["target"] != "unit":
                _fail("temporal matching requires unit target")
            policy = _object(case["temporal_policy"], {"interval_iou", "timestamp_tolerance_ms"})
            threshold = policy["interval_iou"]
            if type(threshold) not in (int, float) or not 0 < threshold <= 1 or not math.isfinite(threshold):
                _fail("invalid interval IoU threshold")
            _integer(policy["timestamp_tolerance_ms"])
        elif case["temporal_policy"] is not None:
            _fail("identity case cannot carry temporal policy")
        judgments = [_validate_item(j, case, judgment=True) for j in _array(case["judgments"])]
        _unique([j["judgment_id"] for j in judgments], "duplicate judgment ID")
        _unique([_signature(j, case) for j in judgments], "duplicate judgment target")
        ranked = [_validate_item(r, case, judgment=False) for r in _array(case["ranked"])]
        if case["target"] == "resource":
            _unique([r["resource_id"] for r in ranked], "duplicate ranked resource")
        else:
            _unique([r["unit_id"] for r in ranked], "duplicate ranked unit")
        if any(_owner(r) not in owners for r in ranked):
            _fail("ranked result outside declared scope")
        positive = [j for j in judgments if _owner(j) in owners and j["grade"] > 0]
        if (case["case_type"] == "positive") != bool(positive):
            _fail("declared case type disagrees with scoped positive gold")
        scoped_gold = [j for j in judgments if _owner(j) in owners]
        if case["match"] == "identity" and case["target"] == "unit":
            _unique([j["unit_id"] for j in scoped_gold], "duplicate scoped unit identity")
        if case["unjudged_policy"] == "error" and any(
            not any(_affinity(r, j, case) is not None for j in scoped_gold) for r in ranked
        ):
            _fail("complete judgments do not cover ranked results")
    _unique(case_ids, "duplicate case ID")
    return deepcopy(value)


def _signature(item: dict[str, Any], case: dict[str, Any]) -> tuple[Any, ...]:
    if case["match"] == "identity":
        return _key(item, case["target"])
    return (*_owner(item), tuple(tuple(p) for p in item["intervals"]), item["timestamp_ms"])


def _affinity(ranked: dict[str, Any], gold: dict[str, Any], case: dict[str, Any]) -> float | None:
    if _owner(ranked) != _owner(gold):
        return None
    if case["match"] == "identity":
        return 1.0 if _key(ranked, case["target"]) == _key(gold, case["target"]) else None
    if ranked["timestamp_ms"] is not None and gold["timestamp_ms"] is not None:
        distance = abs(ranked["timestamp_ms"] - gold["timestamp_ms"])
        return -float(distance) if distance <= case["temporal_policy"]["timestamp_tolerance_ms"] else None
    if not ranked["intervals"] or not gold["intervals"]:
        return None
    left, right = ranked["intervals"], gold["intervals"]
    intersection = sum(max(0, min(b, d) - max(a, c)) for a, b in left for c, d in right)
    union = sum(b - a for a, b in left) + sum(d - c for c, d in right) - intersection
    iou = intersection / union
    return iou if iou >= case["temporal_policy"]["interval_iou"] else None


def _match(
    case: dict[str, Any],
    gold: list[dict[str, Any]],
) -> tuple[list[int], list[int | None], list[dict[str, Any] | None]]:
    """Rank-greedy, highest affinity, then lexical judgment ID; consume positives once."""
    consumed: set[str] = set()
    grades: list[int] = []
    labels: list[int | None] = []
    pairs: list[dict[str, Any] | None] = []
    for ranked in case["ranked"]:
        eligible = [(j, score) for j in gold if (score := _affinity(ranked, j, case)) is not None]
        positives = [(j, score) for j, score in eligible if j["grade"] > 0]
        labels.append(1 if positives else (0 if eligible else None))
        candidates = [(j, score) for j, score in positives if j["judgment_id"] not in consumed]
        if not candidates:
            grades.append(0)
            pairs.append(None)
            continue
        chosen, affinity = min(candidates, key=lambda pair: (-pair[1], pair[0]["judgment_id"]))
        consumed.add(chosen["judgment_id"])
        grades.append(chosen["grade"])
        if case["match"] == "temporal":
            if chosen["intervals"]:
                pairs.append(
                    {
                        "iou": affinity,
                        "start": abs(ranked["intervals"][0][0] - chosen["intervals"][0][0]),
                        "end": abs(ranked["intervals"][-1][1] - chosen["intervals"][-1][1]),
                        "timestamp": None,
                    }
                )
            else:
                pairs.append({"iou": None, "start": None, "end": None, "timestamp": -affinity})
        else:
            pairs.append(None)
    return grades, labels, pairs


def _temporal_summary(pairs: list[dict[str, Any] | None], positive_count: int) -> dict[str, Any]:
    matches = [p for p in pairs if p is not None]
    intervals = [p for p in matches if p["iou"] is not None]
    timestamps = [p for p in matches if p["timestamp"] is not None]
    return {
        "gold": positive_count,
        "matches": len(matches),
        "hit": int(bool(matches)),
        "recall": len(matches) / positive_count if positive_count else None,
        "interval_matches": len(intervals),
        "timestamp_matches": len(timestamps),
        "mean_iou": sum(p["iou"] for p in intervals) / len(intervals) if intervals else None,
        "mean_start_error_ms": sum(p["start"] for p in intervals) / len(intervals) if intervals else None,
        "mean_end_error_ms": sum(p["end"] for p in intervals) / len(intervals) if intervals else None,
        "mean_timestamp_error_ms": sum(p["timestamp"] for p in timestamps) / len(timestamps) if timestamps else None,
    }


def _dcg(grades: list[int]) -> float:
    return sum((2.0**grade - 1.0) / math.log2(rank + 2) for rank, grade in enumerate(grades))


def _evaluate_case(case: dict[str, Any], ordinal: int) -> dict[str, Any]:
    scope = {_owner(owner) for owner in case["scope"]}
    gold = [j for j in case["judgments"] if _owner(j) in scope]
    positive = [j for j in gold if j["grade"] > 0]
    grades, labels, pairs = _match(case, gold)
    if case["unjudged_policy"] == "error" and any(label is None for label in labels):
        _fail("complete judgments do not cover ranked results")
    rows = []
    for k in case["cutoffs"]:
        top = grades[:k]
        hits = sum(g > 0 for g in top)
        first = next((i for i, grade in enumerate(top, 1) if grade > 0), None)
        ideal = _dcg(sorted([j["grade"] for j in positive], reverse=True)[:k])
        judged = sum(label is not None for label in labels[:k])
        is_positive = case["case_type"] == "positive"
        rows.append(
            {
                "k": k,
                "returned": len(top),
                "judged_returned": judged,
                "unjudged_returned": len(top) - judged,
                "zero_grade_returned": sum(label == 0 for label in labels[:k]),
                "judged_fraction": judged / len(top) if top else None,
                "relevant_hits": hits if is_positive else None,
                "recall": hits / len(positive) if is_positive else None,
                "mrr": (1.0 / first if first else 0.0) if is_positive else None,
                "ndcg": _dcg(top) / ideal if is_positive else None,
                "hit": int(hits > 0) if is_positive else None,
                "negative_returned": len(top) if not is_positive else None,
                "empty": int(not top) if not is_positive else None,
                "temporal": _temporal_summary(pairs[:k], len(positive)) if case["match"] == "temporal" else None,
            }
        )
    return {
        "case_ordinal": ordinal,
        "case_type": case["case_type"],
        "target": case["target"],
        "match": case["match"],
        "judgment_policy": case["judgment_policy"],
        "unjudged_policy": case["unjudged_policy"],
        "recall_basis": "complete_gold" if case["judgment_policy"] == "complete" else "judged_pool",
        "temporal_policy": case["temporal_policy"],
        "positive_gold": len(positive),
        "excluded_judgments": len(case["judgments"]) - len(gold),
        "ranked_count": len(case["ranked"]),
        "cutoffs": rows,
    }


def _aggregate(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for result in results:
        for row in result["cutoffs"]:
            key = (
                result["case_type"],
                result["target"],
                result["match"],
                result["judgment_policy"],
                row["k"],
                fingerprint(result["temporal_policy"]),
            )
            groups.setdefault(key, []).append(row)
    summaries = []
    for key, rows in sorted(groups.items()):
        names = [name for name in rows[0] if name not in {"k", "temporal"}]
        values = {name: [r[name] for r in rows if r[name] is not None] for name in names}
        if rows[0]["temporal"] is not None:
            for name in rows[0]["temporal"]:
                values[f"temporal_{name}"] = [r["temporal"][name] for r in rows if r["temporal"][name] is not None]
        summaries.append(
            {
                "case_type": key[0],
                "target": key[1],
                "match": key[2],
                "judgment_policy": key[3],
                "k": key[4],
                "temporal_policy_ref": key[5],
                "cases": len(rows),
                "means": {name: sum(v) / len(v) if v else None for name, v in values.items()},
                "samples": {name: len(v) for name, v in values.items()},
            }
        )
    return summaries


def evaluate_quality_v2(document: object) -> dict[str, Any]:
    """Evaluate one validated v2 document, with ordinal-only privacy-safe output."""
    started = time.perf_counter_ns()
    value = validate_quality_input(document)
    results = [_evaluate_case(case, i) for i, case in enumerate(value["cases"], 1)]
    return {
        "contract": REPORT_CONTRACT,
        "schema_version": 2,
        "report_type": "graded_retrieval_quality",
        "evaluator_version": EVALUATOR_VERSION,
        "input_ref": fingerprint(value),
        "corpus_ref": value["corpus_ref"],
        "judgments_ref": value["judgments_ref"],
        "implementation_ref": value["implementation_ref"],
        "cases": len(results),
        "results": results,
        "summary": _aggregate(results),
        "evaluation_elapsed_ms": (time.perf_counter_ns() - started) / 1_000_000,
        "evidence_boundary": "offline/already-ranked",
        "search_latency_measured": False,
    }


__all__ = ["EVALUATOR_VERSION", "evaluate_quality_v2", "validate_quality_input"]
