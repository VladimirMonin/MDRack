"""Single application-level text, semantic, and hybrid retrieval path."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from mdrack.domain.profiles import IncompatibleEmbeddingProfileError
from mdrack.domain.retrieval import RetrievalCandidate, RetrievalItem, RetrievalResult
from mdrack.embeddings.protocol import EmbeddingError, EmbeddingProvider
from mdrack.ports.storage import RetrievalStorage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Fused:
    candidate: RetrievalCandidate
    text_rank: int | None
    semantic_rank: int | None
    text_score: float | None
    semantic_score: float | None
    rrf_score: float
    first_seen: int


def _fuse_candidates(
    text_candidates: list[RetrievalCandidate],
    semantic_candidates: list[RetrievalCandidate],
    *,
    rrf_k: int,
) -> list[_Fused]:
    text_ranks, text_scores = RetrievalService._first_ranks_and_scores(text_candidates)
    semantic_ranks, semantic_scores = RetrievalService._first_ranks_and_scores(semantic_candidates)
    candidates: dict[str, RetrievalCandidate] = {}
    first_seen: dict[str, int] = {}
    for ordinal, candidate in enumerate(text_candidates + semantic_candidates):
        candidates.setdefault(candidate.logical_id, candidate)
        first_seen.setdefault(candidate.logical_id, ordinal)

    fused: list[_Fused] = []
    for logical_id, candidate in candidates.items():
        text_rank = text_ranks.get(logical_id)
        semantic_rank = semantic_ranks.get(logical_id)
        rrf_score = (1 / (rrf_k + text_rank) if text_rank is not None else 0.0) + (
            1 / (rrf_k + semantic_rank) if semantic_rank is not None else 0.0
        )
        fused.append(
            _Fused(
                candidate=candidate,
                text_rank=text_rank,
                semantic_rank=semantic_rank,
                text_score=text_scores.get(logical_id),
                semantic_score=semantic_scores.get(logical_id),
                rrf_score=rrf_score,
                first_seen=first_seen[logical_id],
            )
        )
    fused.sort(key=lambda item: (-item.rrf_score, item.first_seen, item.candidate.logical_id))
    return fused


class RetrievalService:
    """Canonical retrieval orchestration over normalized storage candidates."""

    def __init__(
        self,
        storage: RetrievalStorage,
        *,
        embedding_provider: EmbeddingProvider | None = None,
        profile: str = "default",
        profile_fingerprint: str | None = None,
        rrf_k: int = 60,
    ) -> None:
        if rrf_k < 1:
            raise ValueError("rrf_k must be positive")
        self.storage = storage
        self.embedding_provider = embedding_provider
        self.profile = profile
        self.profile_fingerprint = profile_fingerprint
        self.rrf_k = rrf_k

    def search_text(self, query: str, *, limit: int = 20, offset: int = 0) -> RetrievalResult:
        self._validate_page(limit, offset)
        candidates = self.storage.retrieve_text_candidates(query, limit=limit, offset=offset)
        items = tuple(
            self._candidate_item(candidate, text_rank=rank)
            for rank, candidate in enumerate(candidates, start=offset + 1)
        )
        return RetrievalResult(query=query, mode="text", results=items, total_count=len(items))

    async def search_semantic(self, query: str, *, limit: int = 20) -> RetrievalResult:
        self._validate_page(limit, 0)
        candidates, degraded_reason = await self._semantic_candidates(query, limit=limit)
        items = tuple(
            self._candidate_item(candidate, semantic_rank=rank)
            for rank, candidate in enumerate(candidates, start=1)
        )
        return RetrievalResult(
            query=query,
            mode="semantic",
            results=items,
            total_count=len(items),
            degraded=degraded_reason is not None,
            degraded_reason=degraded_reason,
        )

    async def search_hybrid(
        self,
        query: str,
        *,
        limit: int = 20,
        reranker: None = None,
    ) -> RetrievalResult:
        """Fuse text and semantic ranks with RRF; production reranking is deferred."""
        if reranker is not None:
            raise ValueError("reranking is not supported in v0.2")
        self._validate_page(limit, 0)
        candidate_limit = limit * 2
        text_candidates = self.storage.retrieve_text_candidates(query, limit=candidate_limit, offset=0)
        semantic_candidates, degraded_reason = await self._semantic_candidates(query, limit=candidate_limit)
        fused = self._fuse(text_candidates, semantic_candidates)[:limit]
        items = tuple(
            RetrievalItem(
                logical_id=item.candidate.logical_id,
                score=item.rrf_score,
                source_locator=item.candidate.source_locator,
                content_preview=item.candidate.content_preview,
                text_rank=item.text_rank,
                semantic_rank=item.semantic_rank,
                rrf_rank=rank,
                rrf_score=item.rrf_score,
                text_score=item.text_score,
                semantic_score=item.semantic_score,
                metadata=item.candidate.metadata,
            )
            for rank, item in enumerate(fused, start=1)
        )
        return RetrievalResult(
            query=query,
            mode="hybrid",
            results=items,
            total_count=len(items),
            degraded=degraded_reason is not None,
            degraded_reason=degraded_reason,
        )

    async def _semantic_candidates(
        self,
        query: str,
        *,
        limit: int,
    ) -> tuple[list[RetrievalCandidate], str | None]:
        if self.embedding_provider is None:
            logger.warning("retrieval.semantic.degraded reason=embedding_provider_unavailable")
            return [], "embedding_provider_unavailable"
        try:
            query_vector = await self.embedding_provider.embed_query(query, profile=self.profile)
            candidates = self.storage.retrieve_semantic_candidates(
                query_vector,
                profile=self.profile,
                profile_fingerprint=self.profile_fingerprint,
                limit=limit,
            )
        except IncompatibleEmbeddingProfileError:
            logger.warning("retrieval.semantic.degraded reason=incompatible_embedding_profile")
            return [], "incompatible_embedding_profile"
        except EmbeddingError:
            logger.warning("retrieval.semantic.degraded reason=embedding_provider_error")
            return [], "embedding_provider_error"
        except Exception:
            logger.warning("retrieval.semantic.degraded reason=semantic_search_error")
            return [], "semantic_search_error"
        return candidates, None

    def _fuse(
        self,
        text_candidates: list[RetrievalCandidate],
        semantic_candidates: list[RetrievalCandidate],
    ) -> list[_Fused]:
        return _fuse_candidates(text_candidates, semantic_candidates, rrf_k=self.rrf_k)

    @staticmethod
    def _candidate_item(
        candidate: RetrievalCandidate,
        *,
        text_rank: int | None = None,
        semantic_rank: int | None = None,
    ) -> RetrievalItem:
        return RetrievalItem(
            logical_id=candidate.logical_id,
            score=candidate.score,
            source_locator=candidate.source_locator,
            content_preview=candidate.content_preview,
            text_rank=text_rank,
            semantic_rank=semantic_rank,
            text_score=candidate.score if text_rank is not None else None,
            semantic_score=candidate.score if semantic_rank is not None else None,
            metadata=candidate.metadata,
        )

    @staticmethod
    def _first_ranks_and_scores(
        candidates: list[RetrievalCandidate],
    ) -> tuple[dict[str, int], dict[str, float]]:
        ranks: dict[str, int] = {}
        scores: dict[str, float] = {}
        for rank, candidate in enumerate(candidates, start=1):
            ranks.setdefault(candidate.logical_id, rank)
            scores.setdefault(candidate.logical_id, candidate.score)
        return ranks, scores

    @staticmethod
    def _validate_page(limit: int, offset: int) -> None:
        if limit < 1:
            raise ValueError("limit must be positive")
        if offset < 0:
            raise ValueError("offset must not be negative")


class HybridRetrievalService:
    """Compatibility wrapper for callers that already provide both candidate lists."""

    def __init__(self, *, rrf_k: int = 60, reranker: object | None = None) -> None:
        if reranker is not None:
            raise ValueError("reranking is not supported in v0.2")
        self.rrf_k = rrf_k

    async def retrieve(
        self,
        query: str,
        text_candidates: list[RetrievalCandidate],
        semantic_candidates: list[RetrievalCandidate],
        *,
        limit: int,
        rerank_requested: bool = False,
    ) -> RetrievalResult:
        if rerank_requested:
            raise ValueError("reranking is not supported in v0.2")
        fused = _fuse_candidates(text_candidates, semantic_candidates, rrf_k=self.rrf_k)[:limit]
        items = tuple(
            RetrievalItem(
                logical_id=item.candidate.logical_id,
                score=item.rrf_score,
                source_locator=item.candidate.source_locator,
                content_preview=item.candidate.content_preview,
                text_rank=item.text_rank,
                semantic_rank=item.semantic_rank,
                rrf_rank=rank,
                rrf_score=item.rrf_score,
                text_score=item.text_score,
                semantic_score=item.semantic_score,
                metadata=item.candidate.metadata,
            )
            for rank, item in enumerate(fused, start=1)
        )
        return RetrievalResult(query=query, mode="hybrid", results=items, total_count=len(items))
