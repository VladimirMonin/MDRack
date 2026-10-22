"""Manual offline oracle at the actual versioned evaluator/serialization seam."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from mdrack.eval.quality import QualityEvaluationError
from mdrack.eval.quality_v2 import evaluate_quality_v2

SHA = "sha256:" + "a" * 64


def item(name: str, *, resource: str | None = None, revision: str = "v1") -> dict[str, Any]:
    return {
        "resource_id": resource or name,
        "source_revision": revision,
        "unit_id": name,
        "intervals": [],
        "timestamp_ms": None,
    }


def judgment(name: str, grade: int, **kwargs: Any) -> dict[str, Any]:
    return {"judgment_id": name, "grade": grade, **item(name, **kwargs)}


def document(*, target: str = "unit") -> dict[str, Any]:
    case = {
        "case_id": "synthetic-case",
        "case_type": "positive",
        "target": target,
        "match": "identity",
        "cutoffs": [1, 2],
        "scope": [{"resource_id": r, "source_revision": "v1"} for r in ("zero", "positive", "other")],
        "judgment_policy": "pooled",
        "unjudged_policy": "nonrelevant",
        "temporal_policy": None,
        "judgments": [judgment("zero", 0), judgment("positive", 3)],
        "ranked": [item("zero"), item("positive")],
    }
    if target == "resource":
        for j in case["judgments"]:
            j["unit_id"] = None
    return {
        "contract": "mdrack.quality-input",
        "schema_version": 2,
        "corpus_ref": SHA,
        "judgments_ref": SHA,
        "implementation_ref": SHA,
        "cases": [case],
    }


@pytest.mark.parametrize("target", ["resource", "unit"])
def test_versioned_grade_zero_and_cutoff_manual_oracle(target: str) -> None:
    value = document(target=target)
    before = deepcopy(value)
    report = evaluate_quality_v2(value)
    assert value == before
    assert report["schema_version"] == 2
    assert report["report_type"] == "graded_retrieval_quality"
    a, b = report["results"][0]["cutoffs"]
    assert (a["relevant_hits"], a["recall"], a["mrr"], a["ndcg"]) == (0, 0, 0, 0)
    assert (b["relevant_hits"], b["recall"], b["mrr"]) == (1, 1, 0.5)
    assert b["ndcg"] == pytest.approx(0.630929753571)
    assert a["zero_grade_returned"] == 1
    assert b["judged_fraction"] == 1


def test_repeated_resource_is_contract_failure_even_after_cutoff() -> None:
    value = document(target="resource")
    case = value["cases"][0]
    case["cutoffs"] = [1]
    case["ranked"] = [item("first", resource="positive"), item("second", resource="positive")]
    with pytest.raises(QualityEvaluationError, match="duplicate ranked resource"):
        evaluate_quality_v2(value)


def temporal_document() -> dict[str, Any]:
    value = document()
    case = value["cases"][0]
    case["match"] = "temporal"
    case["temporal_policy"] = {"interval_iou": 0.5, "timestamp_tolerance_ms": 10}
    case["scope"] = [{"resource_id": r, "source_revision": v} for r in ("video", "foreign") for v in ("v1", "v2")]
    gold = judgment("gold", 3, resource="video")
    gold.update(unit_id=None, intervals=[[100, 200]])
    case["judgments"] = [gold]
    ranked = item("new-recipe", resource="video")
    ranked["intervals"] = [[100, 200]]
    case["ranked"] = [ranked]
    return value


@pytest.mark.parametrize("change", ["owner", "revision", "miss", "tiny", "long"])
def test_temporal_miss_has_no_hit_or_gain_and_nullable_errors(change: str) -> None:
    value = temporal_document()
    ranked = value["cases"][0]["ranked"][0]
    if change == "owner":
        ranked["resource_id"] = "foreign"
    elif change == "revision":
        ranked["source_revision"] = "v2"
    else:
        ranked["intervals"] = {"miss": [[300, 400]], "tiny": [[199, 200]], "long": [[0, 1000]]}[change]
    row = evaluate_quality_v2(value)["results"][0]["cutoffs"][0]
    assert (row["relevant_hits"], row["recall"], row["mrr"], row["ndcg"]) == (0, 0, 0, 0)
    assert row["temporal"]["matches"] == row["temporal"]["hit"] == 0
    assert row["temporal"]["mean_iou"] is None
    assert row["temporal"]["mean_start_error_ms"] is None
    assert row["temporal"]["mean_end_error_ms"] is None
    assert row["temporal"]["mean_timestamp_error_ms"] is None


def test_temporal_one_to_one_prevents_repeat_gain_and_cross_recipe_id_requirement() -> None:
    value = temporal_document()
    case = value["cases"][0]
    repeat = deepcopy(case["ranked"][0])
    repeat["unit_id"] = "second-overlap"
    case["ranked"].append(repeat)
    a, b = evaluate_quality_v2(value)["results"][0]["cutoffs"]
    for row in (a, b):
        assert (row["relevant_hits"], row["recall"], row["mrr"], row["ndcg"]) == (1, 1, 1, 1)
        assert row["temporal"]["matches"] == 1
        assert row["temporal"]["mean_iou"] == 1
        assert row["temporal"]["mean_start_error_ms"] == 0
    assert b["judged_returned"] == 2  # Judged, but not two independent hits.


def test_temporal_zero_grade_never_creates_a_match() -> None:
    value = temporal_document()
    case = value["cases"][0]
    zero = deepcopy(case["judgments"][0])
    zero.update(judgment_id="zero", grade=0, intervals=[[300, 400]])
    case["judgments"].append(zero)
    case["ranked"][0]["intervals"] = [[300, 400]]
    row = evaluate_quality_v2(value)["results"][0]["cutoffs"][0]
    assert row["zero_grade_returned"] == 1
    assert row["relevant_hits"] == row["temporal"]["matches"] == 0
    assert row["temporal"]["mean_start_error_ms"] is None


def test_matching_ties_use_gold_id_not_input_order_and_rank_greedy() -> None:
    value = temporal_document()
    case = value["cases"][0]
    a = deepcopy(case["judgments"][0])
    b = deepcopy(a)
    a.update(judgment_id="a", grade=1, intervals=[[0, 100]])
    b.update(judgment_id="b", grade=3, intervals=[[100, 200]])
    case["judgments"] = [b, a]
    case["ranked"][0]["intervals"] = [[0, 200]]  # IoU .5 with both: choose a.
    case["ranked"].append({**item("later", resource="video"), "intervals": [[100, 200]]})
    report = evaluate_quality_v2(value)
    first, second = report["results"][0]["cutoffs"]
    assert first["recall"] == 0.5
    assert first["ndcg"] == pytest.approx(1 / 7)
    assert second["relevant_hits"] == 2
    case["judgments"].reverse()
    assert evaluate_quality_v2(value)["results"] == report["results"]


def test_best_affinity_precedes_gold_id_and_boundary_errors_use_actual_pair() -> None:
    value = temporal_document()
    case = value["cases"][0]
    a = deepcopy(case["judgments"][0])
    a.update(judgment_id="a", grade=1, intervals=[[100, 300]])
    b = deepcopy(case["judgments"][0])
    b["judgment_id"] = "b"
    case["judgments"] = [a, b]
    row = evaluate_quality_v2(value)["results"][0]["cutoffs"][0]
    assert row["ndcg"] == 1  # Choose b (IoU 1), not lexical a (IoU .5).
    case["judgments"] = [b]
    case["ranked"][0]["intervals"] = [[120, 200]]
    row = evaluate_quality_v2(value)["results"][0]["cutoffs"][0]
    assert row["temporal"]["mean_iou"] == 0.8
    assert row["temporal"]["mean_start_error_ms"] == 20
    assert row["temporal"]["mean_end_error_ms"] == 0


def test_temporal_summary_excludes_missing_error_samples() -> None:
    value = temporal_document()
    miss = deepcopy(value["cases"][0])
    miss["case_id"] = "miss"
    miss["ranked"] = []
    value["cases"].append(miss)
    report = evaluate_quality_v2(value)
    group = report["summary"][0]
    assert group["cases"] == 2
    assert group["means"]["temporal_recall"] == 0.5
    assert group["samples"]["temporal_recall"] == 2
    assert group["means"]["temporal_mean_start_error_ms"] == 0
    assert group["samples"]["temporal_mean_start_error_ms"] == 1
    assert group["means"]["temporal_mean_timestamp_error_ms"] is None
    assert group["samples"]["temporal_mean_timestamp_error_ms"] == 0


def test_compound_interval_uses_union_not_convex_hull() -> None:
    value = temporal_document()
    case = value["cases"][0]
    case["judgments"][0]["intervals"] = [[0, 10], [90, 100]]
    case["ranked"][0]["intervals"] = [[0, 100]]  # union IoU .2, NOT 1.
    row = evaluate_quality_v2(value)["results"][0]["cutoffs"][0]
    assert row["relevant_hits"] == 0
    case["ranked"][0]["intervals"] = [[0, 10], [90, 100]]
    row = evaluate_quality_v2(value)["results"][0]["cutoffs"][0]
    assert row["relevant_hits"] == row["temporal"]["mean_iou"] == 1


@pytest.mark.parametrize(("distance", "hit"), [(0, 1), (10, 1), (11, 0)])
def test_timestamp_tolerance_is_inclusive_and_errors_nullable(distance: int, hit: int) -> None:
    value = temporal_document()
    case = value["cases"][0]
    case["judgments"][0].update(intervals=[], timestamp_ms=100)
    case["ranked"][0].update(intervals=[], timestamp_ms=100 + distance)
    row = evaluate_quality_v2(value)["results"][0]["cutoffs"][0]
    assert row["relevant_hits"] == hit
    assert row["temporal"]["mean_iou"] is None
    assert row["temporal"]["mean_timestamp_error_ms"] == (distance if hit else None)


@pytest.mark.parametrize("target", ["unit", "resource"])
def test_scope_excludes_positive_gold_and_negative_type_is_explicit(target: str) -> None:
    value = document(target=target)
    case = value["cases"][0]
    case["scope"] = [case["scope"][0]]  # Only the zero-grade resource remains in scope.
    case["ranked"] = [case["ranked"][0]]
    with pytest.raises(QualityEvaluationError, match="declared case type"):
        evaluate_quality_v2(value)
    case["case_type"] = "negative"
    result = evaluate_quality_v2(value)["results"][0]
    assert result["positive_gold"] == 0
    assert result["excluded_judgments"] == 1
    row = result["cutoffs"][0]
    assert row["recall"] is row["mrr"] is row["ndcg"] is row["relevant_hits"] is None
    assert row["negative_returned"] == 1
    assert row["empty"] == 0
    case["ranked"] = []
    report = evaluate_quality_v2(value)
    assert report["results"][0]["cutoffs"][0]["empty"] == 1
    assert report["summary"][0]["samples"]["recall"] == 0
    assert report["summary"][0]["means"]["recall"] is None


def test_resource_scope_cannot_count_two_revisions_as_two_resources() -> None:
    value = document(target="resource")
    case = value["cases"][0]
    case["scope"].append({"resource_id": "positive", "source_revision": "v2"})
    second = deepcopy(case["judgments"][1])
    second.update(judgment_id="second-revision", source_revision="v2")
    case["judgments"].append(second)
    with pytest.raises(QualityEvaluationError, match="resource scope must select one revision"):
        evaluate_quality_v2(value)


def test_pooled_coverage_distinguishes_zero_from_unjudged_without_completeness_claim() -> None:
    value = document()
    case = value["cases"][0]
    case["ranked"] = [item("other"), item("zero")]
    result = evaluate_quality_v2(value)["results"][0]
    assert result["recall_basis"] == "judged_pool"
    row = result["cutoffs"][1]
    assert (row["unjudged_returned"], row["zero_grade_returned"], row["judged_fraction"]) == (1, 1, 0.5)
    assert row["relevant_hits"] == 0
    case.update(judgment_policy="complete", unjudged_policy="error")
    with pytest.raises(QualityEvaluationError, match="complete judgments"):
        evaluate_quality_v2(value)
    case["judgments"].append(judgment("other", 0))
    assert evaluate_quality_v2(value)["results"][0]["recall_basis"] == "complete_gold"


def test_unit_identity_gold_cannot_count_one_unit_id_as_two_units() -> None:
    value = document()
    case = value["cases"][0]
    case["scope"].append({"resource_id": "positive", "source_revision": "v2"})
    second = deepcopy(case["judgments"][1])
    second.update(judgment_id="second-revision", source_revision="v2")
    case["judgments"].append(second)
    with pytest.raises(QualityEvaluationError, match="duplicate scoped unit identity"):
        evaluate_quality_v2(value)


def test_two_positive_gold_denominator_and_empty_gold_conventions() -> None:
    value = document()
    case = value["cases"][0]
    case["judgments"].append(judgment("other", 1))
    case["ranked"] = [item("other")]
    row = evaluate_quality_v2(value)["results"][0]["cutoffs"][0]
    assert row["recall"] == 0.5
    assert row["mrr"] == 1
    assert row["ndcg"] == pytest.approx(1 / 7)
    case["judgments"] = []
    with pytest.raises(QualityEvaluationError, match="declared case type"):
        evaluate_quality_v2(value)
    case["case_type"] = "negative"
    row = evaluate_quality_v2(value)["results"][0]["cutoffs"][0]
    assert row["negative_returned"] == row["unjudged_returned"] == 1


def test_summary_does_not_mix_targets_case_types_or_temporal_policy() -> None:
    value = temporal_document()
    alternate = deepcopy(value["cases"][0])
    alternate["case_id"] = "alternate-policy"
    alternate["temporal_policy"]["interval_iou"] = 0.75
    value["cases"].append(alternate)
    identity = document(target="resource")["cases"][0]
    identity["case_id"] = "identity"
    value["cases"].append(identity)
    negative = deepcopy(identity)
    negative.update(case_id="negative", case_type="negative", judgments=[], ranked=[])
    value["cases"].append(negative)
    report = evaluate_quality_v2(value)
    assert len(report["summary"]) == 8  # Four distinct groups at each cutoff.
    assert all(g["cases"] == 1 for g in report["summary"])
    assert report["search_latency_measured"] is False
    assert "search_latency_ms" not in report


@pytest.mark.parametrize(
    "mutation",
    [
        "target",
        "cutoff-bool",
        "cutoff-zero",
        "cutoff-duplicate",
        "cutoff-order",
        "grade-bool",
        "grade-negative",
        "grade-large",
        "unknown",
        "missing",
        "duplicate-unit",
        "duplicate-judgment",
        "duplicate-gold-id",
        "foreign-rank",
        "duplicate-scope",
        "negative-positive",
        "policy",
        "case-id",
    ],
)
def test_invalid_identity_contracts_fail_closed_without_echo(mutation: str) -> None:
    value = document()
    case = value["cases"][0]
    if mutation == "target":
        case["target"] = "PRIVATE_SENTINEL"
    elif mutation.startswith("cutoff-"):
        case["cutoffs"] = {
            "cutoff-bool": [True],
            "cutoff-zero": [0],
            "cutoff-duplicate": [1, 1],
            "cutoff-order": [2, 1],
        }[mutation]
    elif mutation.startswith("grade-"):
        case["judgments"][0]["grade"] = {"grade-bool": True, "grade-negative": -1, "grade-large": 4}[mutation]
    elif mutation == "unknown":
        case["PRIVATE_SENTINEL"] = "PRIVATE_SENTINEL"
    elif mutation == "missing":
        del case["target"]
    elif mutation == "duplicate-unit":
        case["ranked"].append(deepcopy(case["ranked"][0]))
    elif mutation == "duplicate-judgment":
        j = deepcopy(case["judgments"][0])
        j["judgment_id"] = "different-id"
        case["judgments"].append(j)
    elif mutation == "duplicate-gold-id":
        case["judgments"][0]["judgment_id"] = case["judgments"][1]["judgment_id"]
    elif mutation == "foreign-rank":
        case["ranked"][0]["resource_id"] = "PRIVATE_SENTINEL"
    elif mutation == "duplicate-scope":
        case["scope"].append(deepcopy(case["scope"][0]))
    elif mutation == "negative-positive":
        case["case_type"] = "negative"
    elif mutation == "policy":
        case["unjudged_policy"] = "error"
    else:
        value["cases"].append(deepcopy(case))
    with pytest.raises(QualityEvaluationError) as error:
        evaluate_quality_v2(value)
    assert "PRIVATE_SENTINEL" not in str(error.value)


@pytest.mark.parametrize(
    "mutation",
    [
        "threshold-bool",
        "threshold-zero",
        "threshold-nan",
        "tolerance-negative",
        "overlap",
        "touching",
        "unsorted",
        "empty",
        "both",
        "resource",
        "unit-gold",
    ],
)
def test_invalid_temporal_contracts_fail_closed(mutation: str) -> None:
    value = temporal_document()
    case = value["cases"][0]
    if mutation.startswith("threshold-"):
        case["temporal_policy"]["interval_iou"] = {
            "threshold-bool": True,
            "threshold-zero": 0,
            "threshold-nan": float("nan"),
        }[mutation]
    elif mutation == "tolerance-negative":
        case["temporal_policy"]["timestamp_tolerance_ms"] = -1
    elif mutation in {"overlap", "touching", "unsorted", "empty"}:
        case["ranked"][0]["intervals"] = {
            "overlap": [[0, 20], [10, 30]],
            "touching": [[0, 10], [10, 20]],
            "unsorted": [[30, 40], [10, 20]],
            "empty": [[0, 0]],
        }[mutation]
    elif mutation == "both":
        case["ranked"][0]["timestamp_ms"] = 0
    elif mutation == "resource":
        case["target"] = "resource"
    else:
        case["judgments"][0]["unit_id"] = "must-not-require-old-recipe"
    with pytest.raises(QualityEvaluationError):
        evaluate_quality_v2(value)
