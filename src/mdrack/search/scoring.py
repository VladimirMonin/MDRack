"""Reciprocal Rank Fusion (RRF) scoring for hybrid search."""

from __future__ import annotations

from dataclasses import dataclass

from mdrack.search.semantic import SemanticSearchResultItem
from mdrack.search.text import TextSearchItem


@dataclass
class RankedResult:
    """A chunk with its combined RRF score and individual ranks."""

    chunk_id: str
    combined_score: float
    text_rank: int | None
    semantic_rank: int | None
    rrf_rank: int = 0


def reciprocal_rank_fusion(
    text_results: list[TextSearchItem],
    semantic_results: list[SemanticSearchResultItem],
    k: int = 60,
) -> list[RankedResult]:
    """Combine ranked lists using Reciprocal Rank Fusion (RRF).

    Each result from both lists contributes a score: 1 / (k + rank).
    Ranks are 1-based (first item has rank=1, second rank=2, etc.).
    If a chunk appears in both lists, its scores are summed.
    The final list is sorted by combined_score descending.

    Args:
        text_results: Results from text search (ordered by relevance).
        semantic_results: Results from semantic search (ordered by similarity).
        k: RRF constant that controls the influence of rank vs. score.
           Default 60; smaller k gives more weight to top-ranked items.

    Returns:
        List of RankedResult sorted by combined_score descending.
    """
    # Preserve the first occurrence of duplicates and a deterministic tie order.
    text_ranks: dict[str, int] = {}
    semantic_ranks: dict[str, int] = {}
    first_seen: dict[str, int] = {}
    for idx, item in enumerate(text_results, start=1):
        text_ranks.setdefault(item.chunk_id, idx)
        first_seen.setdefault(item.chunk_id, idx - 1)
    for idx, item in enumerate(semantic_results, start=1):
        semantic_ranks.setdefault(item.chunk_id, idx)
        first_seen.setdefault(item.chunk_id, len(text_results) + idx - 1)

    all_chunk_ids = list(dict.fromkeys([*text_ranks, *semantic_ranks]))

    # Compute combined score for each chunk
    results: list[RankedResult] = []
    for chunk_id in all_chunk_ids:
        score = 0.0
        text_rank = text_ranks.get(chunk_id)
        semantic_rank = semantic_ranks.get(chunk_id)

        if text_rank is not None:
            score += 1.0 / (k + text_rank)
        if semantic_rank is not None:
            score += 1.0 / (k + semantic_rank)

        results.append(
            RankedResult(
                chunk_id=chunk_id,
                combined_score=score,
                text_rank=text_rank,
                semantic_rank=semantic_rank,
            )
        )

    results.sort(key=lambda r: (-r.combined_score, first_seen[r.chunk_id], r.chunk_id))
    for rank, result in enumerate(results, start=1):
        result.rrf_rank = rank

    return results
