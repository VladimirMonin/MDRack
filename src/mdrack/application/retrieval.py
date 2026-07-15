"""Provider-neutral hybrid fusion and optional fail-open reranking."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from mdrack.domain.retrieval import (
    HybridRetrievalItem,
    HybridRetrievalResult,
    RerankingStatus,
    RetrievalCandidate,
)
from mdrack.ports.reranker import RerankDocument, RerankerError, RerankerProvider, RerankerUnavailable

logger = logging.getLogger(__name__)

_SAFE_RERANKER_FAILURE_REASONS = frozenset({"provider_error", "unsupported_by_runtime"})


@dataclass(frozen=True)
class _Fused:
    candidate: RetrievalCandidate
    text_rank: int | None
    semantic_rank: int | None
    score: float
    first_seen: int


class HybridRetrievalService:
    def __init__(self, *, rrf_k: int = 60, reranker: RerankerProvider | None = None) -> None:
        if rrf_k < 1:
            raise ValueError("rrf_k must be positive")
        self.rrf_k = rrf_k
        self.reranker = reranker

    async def retrieve(
        self,
        query: str,
        text_candidates: list[RetrievalCandidate],
        semantic_candidates: list[RetrievalCandidate],
        *,
        limit: int,
        rerank_requested: bool = False,
    ) -> HybridRetrievalResult:
        if limit < 1:
            raise ValueError("limit must be positive")
        fused = self._fuse(text_candidates, semantic_candidates)[:limit]
        base_items = tuple(
            HybridRetrievalItem(
                candidate_id=item.candidate.candidate_id,
                text_rank=item.text_rank,
                semantic_rank=item.semantic_rank,
                rrf_rank=rank,
                rrf_score=item.score,
                metadata=item.candidate.metadata,
            )
            for rank, item in enumerate(fused, start=1)
        )
        if not rerank_requested:
            return HybridRetrievalResult(base_items, RerankingStatus(False, False, False))
        if self.reranker is None:
            return self._degraded(base_items, "unsupported_by_runtime")

        documents = [RerankDocument(item.candidate.candidate_id, item.candidate.rerank_text) for item in fused]
        logger.info("rerank.request.started", extra={"candidate_count": len(documents)})
        try:
            scores = await self.reranker.rerank(query, documents, top_n=len(documents))
            score_ids = [score.candidate_id for score in scores]
            known_ids = {document.candidate_id for document in documents}
            response_is_invalid = (
                len(score_ids) != len(known_ids)
                or len(score_ids) != len(set(score_ids))
                or set(score_ids) != known_ids
                or any(
                    isinstance(score.score, bool)
                    or not isinstance(score.score, (int, float))
                    or not math.isfinite(score.score)
                    for score in scores
                )
            )
            if response_is_invalid:
                raise RerankerError("invalid_response")
        except RerankerUnavailable as exc:
            reason = self._safe_failure_reason(exc.reason)
            logger.warning("rerank.request.degraded", extra={"reason": reason, "candidate_count": len(documents)})
            return self._degraded(base_items, reason)
        except Exception:
            logger.warning(
                "rerank.request.degraded",
                extra={"reason": "provider_error", "candidate_count": len(documents)},
            )
            return self._degraded(base_items, "provider_error")

        by_id = {item.candidate_id: item for item in base_items}
        ordered: list[HybridRetrievalItem] = []
        for rank, score in enumerate(scores, start=1):
            item = by_id.pop(score.candidate_id)
            ordered.append(
                HybridRetrievalItem(
                    candidate_id=item.candidate_id,
                    text_rank=item.text_rank,
                    semantic_rank=item.semantic_rank,
                    rrf_rank=item.rrf_rank,
                    rrf_score=item.rrf_score,
                    rerank_rank=rank,
                    rerank_score=score.score,
                    metadata=item.metadata,
                )
            )
        ordered.extend(sorted(by_id.values(), key=lambda item: item.rrf_rank))
        logger.info("rerank.request.finished", extra={"candidate_count": len(documents), "result_count": len(ordered)})
        return HybridRetrievalResult(tuple(ordered), RerankingStatus(True, True, False))

    def _fuse(
        self,
        text_candidates: list[RetrievalCandidate],
        semantic_candidates: list[RetrievalCandidate],
    ) -> list[_Fused]:
        text_ranks = self._first_ranks(text_candidates)
        semantic_ranks = self._first_ranks(semantic_candidates)
        candidates: dict[str, RetrievalCandidate] = {}
        first_seen: dict[str, int] = {}
        for ordinal, candidate in enumerate(text_candidates + semantic_candidates):
            candidates.setdefault(candidate.candidate_id, candidate)
            first_seen.setdefault(candidate.candidate_id, ordinal)
        fused: list[_Fused] = []
        for candidate_id, candidate in candidates.items():
            text_rank = text_ranks.get(candidate_id)
            semantic_rank = semantic_ranks.get(candidate_id)
            score = (1 / (self.rrf_k + text_rank) if text_rank is not None else 0.0) + (
                1 / (self.rrf_k + semantic_rank) if semantic_rank is not None else 0.0
            )
            fused.append(_Fused(candidate, text_rank, semantic_rank, score, first_seen[candidate_id]))
        fused.sort(key=lambda item: (-item.score, item.first_seen, item.candidate.candidate_id))
        return fused

    @staticmethod
    def _first_ranks(candidates: list[RetrievalCandidate]) -> dict[str, int]:
        ranks: dict[str, int] = {}
        for rank, candidate in enumerate(candidates, start=1):
            ranks.setdefault(candidate.candidate_id, rank)
        return ranks

    @staticmethod
    def _safe_failure_reason(reason: str) -> str:
        if reason in _SAFE_RERANKER_FAILURE_REASONS:
            return reason
        return "provider_error"

    @staticmethod
    def _degraded(
        items: tuple[HybridRetrievalItem, ...],
        reason: str,
    ) -> HybridRetrievalResult:
        return HybridRetrievalResult(items, RerankingStatus(True, False, True, reason))
