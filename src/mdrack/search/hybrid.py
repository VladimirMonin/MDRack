"""Legacy hybrid wrapper over application-level reciprocal rank fusion."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from mdrack.adapters.sqlite.index_storage import SQLiteIndexStorage
from mdrack.application.retrieval import RetrievalService
from mdrack.config.models import MDRackConfig
from mdrack.domain.indexing import SourceLocator
from mdrack.ports.embeddings import EmbeddingProvider


@dataclass
class HybridSearchResultItem:
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
    rrf_rank: int = 0
    rrf_score: float = 0.0
    rerank_rank: int | None = None
    rerank_score: float | None = None
    source_locator: SourceLocator | None = None


@dataclass
class HybridSearchResult:
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
    """Compatibility wrapper; RRF is performed only by ``RetrievalService``."""
    if not query.strip():
        return HybridSearchResult(query=query.strip(), results=[], total_count=0)
    result = await RetrievalService(
        SQLiteIndexStorage(conn),
        embedding_provider=provider,
        profile="default",
        rrf_k=config.search.rrf_k,
    ).search_hybrid(query, limit=limit, reranker=None)
    items = [
        HybridSearchResultItem(
            chunk_id=item.logical_id,
            combined_score=item.score,
            text_rank=item.text_rank,
            semantic_rank=item.semantic_rank,
            text_score=item.text_score,
            semantic_score=item.semantic_score,
            content_preview=item.content_preview,
            file_relative_path=item.source_locator.relative_path,
            section_title=item.metadata.get("section_title"),
            heading_path=item.metadata.get("heading_path"),
            rrf_rank=item.rrf_rank or 0,
            rrf_score=item.rrf_score or 0.0,
            source_locator=item.source_locator,
        )
        for item in result.results
    ]
    return HybridSearchResult(
        query=result.query,
        results=items,
        total_count=result.total_count,
        error=result.degraded_reason,
        degraded=result.degraded,
    )
