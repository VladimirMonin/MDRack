"""Retrieval evaluation metrics: Recall@K, MRR, Precision@K, nDCG@K."""

from __future__ import annotations

import math
from collections.abc import Mapping


def recall_at_k(expected_ids: set[str], retrieved_ids: list[str], k: int) -> float:
    """Compute Recall@K — fraction of expected items found in top-K retrieved."""
    if not expected_ids:
        return 0.0
    found = len(expected_ids.intersection(retrieved_ids[:k]))
    return found / len(expected_ids)


def mrr(expected_ids: set[str], retrieved_ids: list[str]) -> float:
    """Compute reciprocal rank of the first relevant result."""
    if not expected_ids:
        return 0.0
    for rank, chunk_id in enumerate(retrieved_ids, start=1):
        if chunk_id in expected_ids:
            return 1.0 / rank
    return 0.0


def precision_at_k(expected_ids: set[str], retrieved_ids: list[str], k: int) -> float:
    """Compute Precision@K — fraction of returned top-K items that are relevant."""
    if k <= 0:
        return 0.0
    top_k = retrieved_ids[:k]
    if not top_k:
        return 0.0
    found = sum(1 for chunk_id in top_k if chunk_id in expected_ids)
    return found / len(top_k)


def ndcg_at_k(
    relevance_by_id: Mapping[str, float],
    retrieved_ids: list[str],
    k: int,
) -> float:
    """Compute normalized discounted cumulative gain at cutoff *k*.

    Relevance values may be binary or graded. Non-positive and missing grades
    contribute no gain. A query without positive relevance has an nDCG of 0.
    """
    if k <= 0:
        return 0.0

    def discounted_gain(grades: list[float]) -> float:
        return float(
            sum(
                (2.0 ** max(grade, 0.0) - 1.0) / math.log2(rank + 2)
                for rank, grade in enumerate(grades)
            )
        )

    seen: set[str] = set()
    actual: list[float] = []
    for item_id in retrieved_ids[:k]:
        if item_id in seen:
            actual.append(0.0)
            continue
        seen.add(item_id)
        actual.append(float(relevance_by_id.get(item_id, 0.0)))
    ideal = sorted(
        (max(float(grade), 0.0) for grade in relevance_by_id.values()),
        reverse=True,
    )[:k]
    ideal_gain = discounted_gain(ideal)
    if ideal_gain == 0.0:
        return 0.0
    return float(discounted_gain(actual) / ideal_gain)
