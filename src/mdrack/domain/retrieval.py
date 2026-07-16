"""Canonical provider-neutral retrieval contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping

from mdrack.domain.indexing import SourceLocator

RetrievalMode = Literal["text", "semantic", "hybrid"]


@dataclass(frozen=True)
class RetrievalCandidate:
    """Normalized candidate returned by a storage adapter."""

    logical_id: str
    score: float
    content_preview: str
    source_locator: SourceLocator
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.logical_id:
            raise ValueError("logical_id is required")
        if self.source_locator.chunk_id != self.logical_id:
            raise ValueError("source locator chunk_id must match logical_id")


@dataclass(frozen=True)
class RetrievalItem:
    """One public result shared by text, semantic, and hybrid retrieval."""

    logical_id: str
    score: float
    source_locator: SourceLocator
    content_preview: str
    text_rank: int | None = None
    semantic_rank: int | None = None
    rrf_rank: int | None = None
    rrf_score: float | None = None
    text_score: float | None = None
    semantic_score: float | None = None
    rerank_rank: int | None = None
    rerank_score: float | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def chunk_id(self) -> str:
        """Compatibility alias that still exposes the public logical ID."""
        return self.logical_id

    def to_dict(self) -> dict[str, object]:
        return {
            "logical_id": self.logical_id,
            "chunk_id": self.logical_id,
            "score": self.score,
            "text_score": self.text_score,
            "semantic_score": self.semantic_score,
            "text_rank": self.text_rank,
            "semantic_rank": self.semantic_rank,
            "rrf_rank": self.rrf_rank,
            "rrf_score": self.rrf_score,
            "rerank_rank": self.rerank_rank,
            "rerank_score": self.rerank_score,
            "content_preview": self.content_preview,
            "snippet": self.content_preview,
            "file": self.source_locator.relative_path,
            "section_title": self.metadata.get("section_title"),
            "heading_path": list(self.source_locator.heading_path),
            "source_locator": self.source_locator.to_dict(),
        }


@dataclass(frozen=True)
class RetrievalResult:
    """Canonical application result for every retrieval mode."""

    query: str
    mode: RetrievalMode
    results: tuple[RetrievalItem, ...]
    total_count: int
    degraded: bool = False
    degraded_reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "query": self.query,
            "mode": self.mode,
            "results": [item.to_dict() for item in self.results],
            "total_count": self.total_count,
            "degraded": self.degraded,
            "degraded_reason": self.degraded_reason,
        }


# Compatibility names for integrations that imported the pre-v0.2 hybrid DTOs.
HybridRetrievalItem = RetrievalItem
HybridRetrievalResult = RetrievalResult
