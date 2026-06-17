"""Hybrid search combining text and semantic results via RRF."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass

from mdrack.config.models import MDRackConfig
from mdrack.embeddings.protocol import EmbeddingProvider
from mdrack.search.scoring import reciprocal_rank_fusion
from mdrack.search.semantic import (
    SemanticSearchResult,
)
from mdrack.search.semantic import (
    semantic_search as semantic_search_func,
)
from mdrack.search.text import (
    TextSearchResult,
)
from mdrack.search.text import (
    text_search as text_search_func,
)

logger = logging.getLogger(__name__)


@dataclass
class HybridSearchResultItem:
    """A single hybrid search result with combined provenance."""

    chunk_id: str
    combined_score: float
    text_rank: int | None
    semantic_rank: int | None
    text_score: float | None
    semantic_score: float | None
    content_preview: str
    file_relative_path: str
    section_title: str | None
    heading_path: str | None


@dataclass
class HybridSearchResult:
    """Result of a hybrid search operation."""

    query: str
    results: list[HybridSearchResultItem]
    total_count: int
    error: str | None = None
    degraded: bool = False


async def hybrid_search(
    conn: sqlite3.Connection,
    query: str,
    provider: EmbeddingProvider,
    config: MDRackConfig,
    limit: int = 20,
) -> HybridSearchResult:
    """Perform hybrid search by combining text and semantic search via RRF.

    Runs text_search and semantic_search in parallel, applies Reciprocal Rank
    Fusion to merge the ranked lists, then applies configurable weights to
    the combined scores before final ordering.

    Args:
        conn: SQLite connection.
        query: Search query string.
        provider: Embedding provider for semantic search.
        config: MDRack configuration containing search weights and rrf_k.
        limit: Maximum number of results to return.

    Returns:
        HybridSearchResult with ordered results.
    """
    query = query.strip()
    if not query:
        return HybridSearchResult(query=query, results=[], total_count=0)

    # Run both searches in parallel
    text_limit = limit * 2  # fetch more to ensure good fusion
    semantic_limit = limit * 2

    text_result: TextSearchResult = text_search_func(
        conn, query, limit=text_limit, offset=0
    )
    semantic_result: SemanticSearchResult = await semantic_search_func(
        conn, query, provider, limit=semantic_limit
    )

    if semantic_result.error:
        if not text_result.results:
            return HybridSearchResult(
                query=query,
                results=[],
                total_count=0,
                error=semantic_result.error,
            )
        logger.warning(
            "hybrid.search.degraded reason=semantic_provider_error text_results=%d",
            len(text_result.results),
        )
        return HybridSearchResult(
            query=query,
            results=[
                HybridSearchResultItem(
                    chunk_id=item.chunk_id,
                    combined_score=item.score,
                    text_rank=index,
                    semantic_rank=None,
                    text_score=item.score,
                    semantic_score=None,
                    content_preview=item.snippet,
                    file_relative_path=item.file_relative_path,
                    section_title=item.section_title,
                    heading_path=item.heading_path,
                )
                for index, item in enumerate(text_result.results, start=1)
            ][:limit],
            total_count=min(len(text_result.results), limit),
            error=semantic_result.error,
            degraded=True,
        )

    # If both returned nothing, return empty result
    if not text_result.results and not semantic_result.results:
        return HybridSearchResult(query=query, results=[], total_count=0)

    # Apply RRF to combine ranked lists
    ranked = reciprocal_rank_fusion(
        text_result.results,
        semantic_result.results,
        k=config.search.rrf_k,
    )

    # Apply weights to the combined RRF scores
    weighted_results: list[HybridSearchResultItem] = []
    text_weight = config.search.text_weight
    semantic_weight = config.search.semantic_weight

    # Build lookup maps for original scores and metadata
    text_score_map = {item.chunk_id: item.score for item in text_result.results}
    semantic_score_map = {item.chunk_id: item.score for item in semantic_result.results}

    for ranked_item in ranked:
        chunk_id = ranked_item.chunk_id
        base_score = ranked_item.combined_score

        weighted_score = base_score
        text_score = text_score_map.get(chunk_id)
        semantic_score = semantic_score_map.get(chunk_id)

        # Apply weights: boost based on which sources contributed
        # If chunk appears in both, weight is sum of both weights
        if text_score is not None and semantic_score is not None:
            weighted_score = base_score  # base already sums both ranks; weights are implicit in RRF
        elif text_score is not None:
            weighted_score = base_score * text_weight
        elif semantic_score is not None:
            weighted_score = base_score * semantic_weight

        # Fetch provenance (file/section) and content preview from DB
        # Try to get from semantic first (has content_preview), fall back to text
        provenance_item = None
        for src_item in semantic_result.results + text_result.results:
            if src_item.chunk_id == chunk_id:
                provenance_item = src_item
                break

        content_preview = ""
        file_path = ""
        section_title = None
        heading_path = None

        if provenance_item:
            # SemanticSearchResultItem has content_preview; TextSearchItem has snippet
            if hasattr(provenance_item, "content_preview"):
                content_preview = provenance_item.content_preview
            elif hasattr(provenance_item, "snippet"):
                content_preview = provenance_item.snippet

            file_path = provenance_item.file_relative_path
            section_title = provenance_item.section_title
            heading_path = provenance_item.heading_path

        weighted_results.append(
            HybridSearchResultItem(
                chunk_id=chunk_id,
                combined_score=weighted_score,
                text_rank=ranked_item.text_rank,
                semantic_rank=ranked_item.semantic_rank,
                text_score=text_score,
                semantic_score=semantic_score,
                content_preview=content_preview,
                file_relative_path=file_path,
                section_title=section_title,
                heading_path=heading_path,
            )
        )

    # Re-sort by weighted score descending
    weighted_results.sort(key=lambda r: r.combined_score, reverse=True)

    # Truncate to limit
    final_results = weighted_results[:limit]

    return HybridSearchResult(
        query=query,
        results=final_results,
        total_count=len(final_results),
    )
