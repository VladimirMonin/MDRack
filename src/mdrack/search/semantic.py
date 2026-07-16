"""Legacy semantic search wrapper over the canonical retrieval service."""

from __future__ import annotations

from dataclasses import dataclass

from mdrack.adapters.sqlite.index_storage import SQLiteIndexStorage
from mdrack.application.retrieval import RetrievalService
from mdrack.domain.indexing import SourceLocator
from mdrack.embeddings.protocol import EmbeddingProvider


@dataclass
class SemanticSearchResultItem:
    chunk_id: str
    score: float
    content_preview: str
    file_relative_path: str
    section_title: str | None = None
    heading_path: str | None = None
    source_locator: SourceLocator | None = None


@dataclass
class SemanticSearchResult:
    query: str
    results: list[SemanticSearchResultItem]
    total_count: int
    error: str | None = None


async def semantic_search(
    conn,
    query: str,
    provider: EmbeddingProvider,
    profile: str = "default",
    limit: int = 20,
) -> SemanticSearchResult:
    """Compatibility wrapper; new callers should use ``RetrievalService``."""
    if not query.strip():
        return SemanticSearchResult(query=query.strip(), results=[], total_count=0)
    result = await RetrievalService(
        SQLiteIndexStorage(conn),
        embedding_provider=provider,
        profile=profile,
    ).search_semantic(query, limit=limit)
    items = [
        SemanticSearchResultItem(
            chunk_id=item.logical_id,
            score=item.score,
            content_preview=item.content_preview,
            file_relative_path=item.source_locator.relative_path,
            section_title=item.metadata.get("section_title"),
            heading_path=item.metadata.get("heading_path"),
            source_locator=item.source_locator,
        )
        for item in result.results
    ]
    return SemanticSearchResult(
        query=result.query,
        results=items,
        total_count=result.total_count,
        error=result.degraded_reason,
    )
