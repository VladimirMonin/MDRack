"""Tests for retrieval evaluation metrics."""

from __future__ import annotations

import pytest

from mdrack.eval.metrics import ndcg_at_k, recall_at_k


def test_recall_at_k_counts_each_retrieved_id_only_once() -> None:
    result = recall_at_k({"relevant"}, ["relevant", "relevant"], 2)

    assert result == pytest.approx(1.0)
    assert 0.0 <= result <= 1.0


def test_ndcg_at_k_uses_graded_relevance() -> None:
    relevance = {"high": 3.0, "medium": 2.0, "low": 1.0}

    assert ndcg_at_k(relevance, ["high", "medium", "low"], 3) == pytest.approx(1.0)
    assert ndcg_at_k(relevance, ["low", "medium", "high"], 3) < 1.0


def test_ndcg_at_k_handles_missing_and_invalid_cutoffs() -> None:
    relevance = {"relevant": 1.0}

    assert ndcg_at_k(relevance, ["missing"], 5) == 0.0
    assert ndcg_at_k({}, ["relevant"], 5) == 0.0
    assert ndcg_at_k(relevance, ["relevant"], 0) == 0.0


def test_ndcg_at_k_counts_each_retrieved_id_only_once() -> None:
    relevance = {"relevant": 1.0}

    result = ndcg_at_k(relevance, ["relevant", "relevant"], 2)

    assert result == pytest.approx(1.0)
    assert 0.0 <= result <= 1.0
