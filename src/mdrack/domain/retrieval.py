"""Provider-neutral retrieval result contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class RetrievalCandidate:
    candidate_id: str
    score: float
    rerank_text: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.candidate_id:
            raise ValueError("candidate_id is required")


@dataclass(frozen=True)
class HybridRetrievalItem:
    candidate_id: str
    text_rank: int | None
    semantic_rank: int | None
    rrf_rank: int
    rrf_score: float
    rerank_rank: int | None = None
    rerank_score: float | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RerankingStatus:
    requested: bool
    applied: bool
    degraded: bool
    reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "requested": self.requested,
            "applied": self.applied,
            "degraded": self.degraded,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class HybridRetrievalResult:
    results: tuple[HybridRetrievalItem, ...]
    reranking: RerankingStatus
