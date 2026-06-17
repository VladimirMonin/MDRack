"""Retrieval evaluation metrics: Recall@K, MRR, Precision@K."""

from __future__ import annotations


def recall_at_k(expected_ids: set[str], retrieved_ids: list[str], k: int) -> float:
    """Compute Recall@K — fraction of expected items found in top-K retrieved.

    Args:
        expected_ids: Set of relevant chunk IDs.
        retrieved_ids: Ranked list of retrieved chunk IDs.
        k: Number of top results to consider.

    Returns:
        Recall value in [0.0, 1.0].
    """
    if not expected_ids:
        return 0.0
    top_k = retrieved_ids[:k]
    found = sum(1 for cid in top_k if cid in expected_ids)
    return found / len(expected_ids)


def mrr(expected_ids: set[str], retrieved_ids: list[str]) -> float:
    """Compute Mean Reciprocal Rank (MRR).

    MRR = 1 / rank_of_first_relevant, where rank is 1-indexed.
    Returns 0.0 if no relevant item is retrieved.

    Args:
        expected_ids: Set of relevant chunk IDs.
        retrieved_ids: Ranked list of retrieved chunk IDs.

    Returns:
        MRR value in [0.0, 1.0].
    """
    if not expected_ids:
        return 0.0
    for i, cid in enumerate(retrieved_ids, start=1):
        if cid in expected_ids:
            return 1.0 / i
    return 0.0


def precision_at_k(expected_ids: set[str], retrieved_ids: list[str], k: int) -> float:
    """Compute Precision@K — fraction of top-K retrieved that are relevant.

    Args:
        expected_ids: Set of relevant chunk IDs.
        retrieved_ids: Ranked list of retrieved chunk IDs.
        k: Number of top results to consider.

    Returns:
        Precision value in [0.0, 1.0].
    """
    if k <= 0:
        return 0.0
    top_k = retrieved_ids[:k]
    if not top_k:
        return 0.0
    found = sum(1 for cid in top_k if cid in expected_ids)
    return found / len(top_k)
