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
