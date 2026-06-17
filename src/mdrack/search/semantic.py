"""Semantic search over indexed Markdown content using embeddings."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional

from mdrack.embeddings.protocol import EmbeddingError, EmbeddingProvider
from mdrack.storage.sqlite.vector import VectorIndex

logger = logging.getLogger(__name__)


@dataclass
class SemanticSearchResultItem:
    """A single semantic search result with provenance."""
    chunk_id: str
    score: float
    content_preview: str
    file_relative_path: str
    section_title: Optional[str] = None
    heading_path: Optional[str] = None


@dataclass
class SemanticSearchResult:
    """Result of a semantic search operation."""
    query: str
    results: List[SemanticSearchResultItem]
    total_count: int
    error: Optional[str] = None


async def semantic_search(
    conn,
    query: str,
    provider: EmbeddingProvider,
    profile: str = "default",
    limit: int = 20,
) -> SemanticSearchResult:
    """Perform semantic search using embedding similarity.

    Args:
        conn: SQLite connection.
        query: Search query text.
        provider: Embedding provider to use for query embedding.
        profile: Embedding profile name to search against.
        limit: Maximum number of results to return.

    Returns:
        SemanticSearchResult with matching chunks and metadata.
    """
    query = query.strip()
    if not query:
        return SemanticSearchResult(query=query, results=[], total_count=0)

    try:
        # Embed the query
        query_vector = await provider.embed_query(query, profile=profile)
    except EmbeddingError as e:
        logger.error("semantic.search.failed stage=embed reason=provider_error")
        return SemanticSearchResult(
            query=query, results=[], total_count=0, error=str(e)
        )
    except Exception as e:
        logger.exception("Unexpected error during query embedding")
        return SemanticSearchResult(
            query=query, results=[], total_count=0, error=f"Embedding failed: {e}"
        )

    # Search vector index
    vi = VectorIndex(conn)
    try:
        scored_chunks = vi.search(query_vector, profile_name=profile, limit=limit)
    except Exception as e:
        logger.exception("Vector search failed")
        return SemanticSearchResult(
            query=query, results=[], total_count=0, error=f"Search failed: {e}"
        )

    if not scored_chunks:
        return SemanticSearchResult(query=query, results=[], total_count=0)

    # Enrich results with file and section metadata
    results: List[SemanticSearchResultItem] = []
    for item in scored_chunks:
        chunk_id = item["chunk_id"]
        score = item["score"]

        # Fetch chunk with file and section info
        chunk = conn.execute(
            """
            SELECT
                c.content,
                f.relative_path,
                s.title as section_title,
                s.heading_path as heading_path
            FROM chunks c
            JOIN files f ON c.file_id = f.id
            LEFT JOIN sections s ON c.section_id = s.id
            WHERE c.id = ?
            """,
            (chunk_id,),
        ).fetchone()

        if chunk is None:
            logger.warning("Chunk %s not found or deleted", chunk_id)
            continue

        # Create preview (first 200 chars)
        content = chunk["content"] or ""
        preview = content[:200] + ("..." if len(content) > 200 else "")

        results.append(
            SemanticSearchResultItem(
                chunk_id=chunk_id,
                score=score,
                content_preview=preview,
                file_relative_path=chunk["relative_path"],
                section_title=chunk["section_title"],
                heading_path=chunk["heading_path"],
            )
        )

    return SemanticSearchResult(
        query=query, results=results, total_count=len(results)
    )
