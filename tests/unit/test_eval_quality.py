"""Focused tests for provider-free graded quality evaluation."""

from __future__ import annotations

import pytest

from mdrack.eval.quality import (
    QualityCase,
    QualityEvaluationError,
    QualityJudgment,
    QualityUnit,
    evaluate_quality,
    fingerprint,
)


def _case() -> QualityCase:
    return QualityCase(
        case_kind="timestamp",
        cutoffs=(5, 10),
        mrr_cutoff=10,
        ndcg_cutoff=10,
        slice_tags=("mode:hybrid", "resource:video"),
        judgments=(
            QualityJudgment("resource-1", 3, "unit-good", start_ms=100, end_ms=200),
            QualityJudgment("resource-1", 1, "unit-weak", start_ms=300, end_ms=400),
        ),
    )


def test_evaluate_quality_reports_graded_and_temporal_metrics_without_payloads() -> None:
    ranked = [
        QualityUnit("unit-good", "resource-1", start_ms=100, end_ms=200),
        QualityUnit("unit-weak", "resource-1", start_ms=350, end_ms=450),
        QualityUnit("unit-other", "resource-2", start_ms=0, end_ms=10),
    ]

    report = evaluate_quality(
        [_case()],
        lambda _: ranked,
        corpus_ref="sha256:corpus",
        implementation_ref="sha256:implementation",
    )

    assert report["summary"]["recall_at_5"] == 1.0
    assert report["summary"]["mrr"] == 1.0
    assert report["summary"]["ndcg"] == 1.0
    assert report["summary"]["timestamp_hit_at_k"] == 0
    assert report["summary"]["interval_hit_at_k"] == 1
    assert report["summary"]["best_iou"] == 1.0
    assert report["summary"]["start_error_ms"] == 0
    assert report["summary"]["temporal_cases"] == 1
    assert report["by_slice"]["mode=hybrid"]["cases"] == 1
    assert report["privacy"]["unit_ids_included"] is False
    assert "unit-good" not in str(report)


@pytest.mark.parametrize("resource_target", [False, True], ids=["unit", "resource"])
@pytest.mark.parametrize(
    ("returned", "recall_at_1", "recall_at_2", "mrr", "ndcg"),
    [
        (("zero",), 0.0, 0.0, 0.0, 0.0),
        (("zero", "positive"), 0.0, 1.0, 0.5, 0.630929753571),
        (("positive",), 1.0, 1.0, 1.0, 1.0),
        (("unjudged",), 0.0, 0.0, 0.0, 0.0),
        (("unjudged", "positive"), 0.0, 1.0, 0.5, 0.630929753571),
    ],
    ids=["zero-only", "zero-before-positive", "positive-only", "unjudged-only", "unjudged-before-positive"],
)
def test_only_positive_grades_are_relevant(
    resource_target: bool,
    returned: tuple[str, ...],
    recall_at_1: float,
    recall_at_2: float,
    mrr: float,
    ndcg: float,
) -> None:
    # Synthetic ranked identities, not model responses; both existing target modes.
    case = QualityCase(
        "lexical", (1, 2), 2, 2,
        (
            QualityJudgment("resource-zero", 0, None if resource_target else "zero"),
            QualityJudgment("resource-positive", 3, None if resource_target else "positive"),
        ),
        slice_tags=("mode:text",),
    )
    report = evaluate_quality(
        [case],
        lambda _: [QualityUnit(item, f"resource-{item}") for item in returned],
        corpus_ref="sha256:synthetic",
        implementation_ref="sha256:test",
    )

    for row in (
        report["results"][0], report["summary"],
        report["by_case_kind"]["lexical"], report["by_slice"]["mode=text"],
    ):
        assert row["recall_at_1"] == recall_at_1
        assert row["recall_at_2"] == recall_at_2
        assert row["mrr"] == mrr
        assert row["ndcg"] == ndcg
    assert report["schema_version"] == 1
    assert report["report_type"] == "graded_retrieval_quality"
    assert set(report) == {
        "schema_version", "report_type", "corpus_ref", "implementation_ref",
        "summary", "by_case_kind", "by_slice", "results", "privacy",
    }
    assert set(report["results"][0]) == {
        "case_ordinal", "case_kind", "recall_at_1", "recall_at_2", "mrr", "ndcg",
        "temporal_applicable", "timestamp_hit_at_k", "interval_hit_at_k",
        "best_iou", "start_error_ms", "end_error_ms", "ranked_count",
    }
    assert report["results"][0]["ranked_count"] == len(returned)
    assert report["privacy"] == {
        "raw_queries_included": False, "raw_content_included": False,
        "paths_included": False, "unit_ids_included": False,
    }


@pytest.mark.parametrize("resource_target", [False, True], ids=["unit", "resource"])
def test_recall_denominator_counts_all_positive_judgments(resource_target: bool) -> None:
    case = QualityCase(
        "lexical", (2,), 1, 2,
        tuple(
            QualityJudgment(f"resource-{item}", grade, None if resource_target else item)
            for item, grade in (("zero", 0), ("first", 1), ("second", 3))
        ),
    )
    report = evaluate_quality(
        [case],
        lambda _: [QualityUnit("zero", "resource-zero"), QualityUnit("first", "resource-first")],
        corpus_ref="x", implementation_ref="y",
    )
    assert report["summary"]["recall_at_2"] == 0.5
    assert report["summary"]["mrr"] == 0.0  # Positive at rank 2 is beyond MRR cutoff 1.


@pytest.mark.parametrize("judgments", [(), (QualityJudgment("r", 0, "u"),)])
def test_no_positive_gold_fails_before_ranking(judgments: tuple[QualityJudgment, ...]) -> None:
    def ranker(_: QualityCase) -> tuple[QualityUnit, ...]:
        pytest.fail("zero-gold must fail before calling the ranker")

    with pytest.raises(QualityEvaluationError, match="zero-gold"):
        evaluate_quality(
            [QualityCase("lexical", (1,), 1, 1, judgments)], ranker,
            corpus_ref="x", implementation_ref="y",
        )


def test_zero_gold_and_duplicate_judgments_fail_closed() -> None:
    zero = QualityCase("lexical", (5,), 10, 10, ())
    with pytest.raises(QualityEvaluationError, match="zero-gold"):
        evaluate_quality([zero], lambda _: (), corpus_ref="x", implementation_ref="y")

    duplicate = QualityCase(
        "lexical",
        (5,),
        10,
        10,
        (QualityJudgment("r", 1, "u"), QualityJudgment("r", 1, "u")),
    )
    with pytest.raises(QualityEvaluationError, match="duplicate judgment"):
        evaluate_quality([duplicate], lambda _: (), corpus_ref="x", implementation_ref="y")


def test_duplicate_ranked_ids_fail_closed() -> None:
    with pytest.raises(QualityEvaluationError, match="duplicate ranked ID"):
        evaluate_quality(
            [_case()],
            lambda _: [
                QualityUnit("unit-good", "resource-1"),
                QualityUnit("unit-good", "resource-1"),
            ],
            corpus_ref="x",
            implementation_ref="y",
        )


def test_temporal_aggregates_use_only_applicable_cases() -> None:
    lexical = QualityCase(
        "lexical", (5,), 5, 5, (QualityJudgment("r", 1, "u"),)
    )
    report = evaluate_quality(
        [lexical, _case()],
        lambda case: [
            QualityUnit("u", "r")
            if case.case_kind == "lexical"
            else QualityUnit("unit-good", "resource-1", start_ms=100, end_ms=200)
        ],
        corpus_ref="x",
        implementation_ref="y",
    )

    assert report["summary"]["cases"] == 2
    assert report["summary"]["temporal_cases"] == 1
    assert report["summary"]["best_iou"] == 1.0


def test_fingerprint_is_stable_and_order_independent_for_mapping_keys() -> None:
    assert fingerprint({"b": 2, "a": 1}) == fingerprint({"a": 1, "b": 2})
    assert fingerprint({"a": 1}) != fingerprint({"a": 2})
