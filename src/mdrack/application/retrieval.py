"""Single application-level text, semantic, and hybrid retrieval path."""

from __future__ import annotations

import logging

from mdrack.application.compatibility import CoreCompatibilityMapper
from mdrack.domain.profiles import IncompatibleEmbeddingProfileError
from mdrack.domain.retrieval import (
    RetrievalCandidate,
    RetrievalItem,
    RetrievalMode,
    RetrievalResult,
)
from mdrack.embeddings.protocol import EmbeddingError, EmbeddingProvider
from mdrack.ports.storage import RetrievalStorage
from mdrack_core.application.retrieval import RetrievalService as CoreRetrievalService
from mdrack_core.domain import (
    TARGET_UNIT,
    BranchExecutionError,
    LexicalBranch,
    RankedCandidate,
    SearchRequest,
    SearchScope,
    VectorBranch,
)

logger = logging.getLogger(__name__)


class InvalidTextSearchError(ValueError):
    """A stable app error for a text branch rejected by the active search adapter."""


class _CandidateSearchPort:
    """Adapt already-ranked legacy candidates to the core fusion owner."""

    def __init__(
        self,
        text_candidates: list[RetrievalCandidate],
        semantic_candidates: list[RetrievalCandidate],
        mapper: CoreCompatibilityMapper,
    ) -> None:
        self._text = self._ranked(text_candidates, "text", mapper)
        self._semantic = self._ranked(semantic_candidates, "semantic", mapper)

    def search_lexical(self, branch: LexicalBranch, *, scope: SearchScope) -> list[RankedCandidate]:
        del scope
        return self._text[: branch.candidate_limit]

    def search_vector(self, branch: VectorBranch, *, scope: SearchScope) -> list[RankedCandidate]:
        del scope
        return self._semantic[: branch.candidate_limit]

    @staticmethod
    def _ranked(
        candidates: list[RetrievalCandidate],
        branch_id: str,
        mapper: CoreCompatibilityMapper,
    ) -> list[RankedCandidate]:
        return [
            RankedCandidate(
                unit_id=candidate.logical_id,
                resource_id=str(candidate.metadata.get("resource_id") or candidate.logical_id),
                representation_id=str(
                    candidate.metadata.get("representation_id") or candidate.logical_id
                ),
                rank=rank,
                raw_score=candidate.score,
                branch_id=branch_id,
                evidence_locator=mapper.core_locator(candidate.source_locator),
                metadata={
                    "content_preview": candidate.content_preview,
                    "heading_path": candidate.source_locator.heading_path,
                    "section_title": candidate.metadata.get("section_title"),
                },
            )
            for rank, candidate in enumerate(candidates, start=1)
        ]


def _fuse_with_core(
    *,
    query: str,
    text_candidates: list[RetrievalCandidate],
    semantic_candidates: list[RetrievalCandidate],
    limit: int,
    rrf_k: int,
    mapper: CoreCompatibilityMapper,
    degraded_reason: str | None = None,
) -> RetrievalResult:
    candidate_limit = max(limit * 2, len(text_candidates), len(semantic_candidates), 1)
    core = CoreRetrievalService(
        _CandidateSearchPort(text_candidates, semantic_candidates, mapper)
    ).search(
        SearchRequest(
            lexical_branches=(
                LexicalBranch("text", query or "compatibility-query", candidate_limit=candidate_limit),
            ),
            vector_branches=(
                VectorBranch(
                    "semantic",
                    "compatibility-space",
                    (0.0,),
                    candidate_limit=candidate_limit,
                ),
            ),
            scope=SearchScope(),
            target=TARGET_UNIT,
            limit=limit,
            rrf_k=rrf_k,
            allow_partial=True,
        )
    )
    return mapper.retrieval_result(
        query=query,
        mode="hybrid",
        result=core,
        degraded_reason=degraded_reason,
    )


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
        self.compatibility_mapper = CoreCompatibilityMapper()

    def search_text(self, query: str, *, limit: int = 20, offset: int = 0) -> RetrievalResult:
        self._validate_page(limit, offset)
        search_core = getattr(self.storage, "search_core", None)
        if callable(search_core):
            try:
                core = self.storage.search_core(
                    SearchRequest(
                        lexical_branches=(
                            LexicalBranch("text", query, candidate_limit=limit + offset),
                        ),
                        vector_branches=(),
                        scope=SearchScope(),
                        target=TARGET_UNIT,
                        limit=limit + offset,
                        rrf_k=self.rrf_k,
                    )
                )
            except (BranchExecutionError, ValueError):
                raise InvalidTextSearchError("invalid_text_query") from None
            return self.compatibility_mapper.retrieval_result(
                query=query,
                mode="text",
                result=core,
                offset=offset,
                limit=limit,
            )
        candidates = self.storage.retrieve_text_candidates(query, limit=limit, offset=offset)
        items = tuple(
            self._candidate_item(candidate, text_rank=rank)
            for rank, candidate in enumerate(candidates, start=offset + 1)
        )
        return RetrievalResult(query=query, mode="text", results=items, total_count=len(items))

    async def search_semantic(self, query: str, *, limit: int = 20) -> RetrievalResult:
        self._validate_page(limit, 0)
        if callable(getattr(self.storage, "search_core", None)):
            vector, degraded_reason = await self._prepare_query_vector(query)
            if vector is None:
                return self._empty_degraded(query, "semantic", degraded_reason)
            space_id = self._resolve_embedding_space()
            if space_id is None:
                return self._empty_degraded(
                    query,
                    "semantic",
                    "incompatible_embedding_profile",
                )
            try:
                core = self.storage.search_core(
                    SearchRequest(
                        lexical_branches=(),
                        vector_branches=(
                            VectorBranch("semantic", space_id, vector, candidate_limit=limit),
                        ),
                        scope=SearchScope(),
                        target=TARGET_UNIT,
                        limit=limit,
                        rrf_k=self.rrf_k,
                    )
                )
            except BranchExecutionError as error:
                return self._empty_degraded(query, "semantic", error.category.value)
            return self.compatibility_mapper.retrieval_result(
                query=query,
                mode="semantic",
                result=core,
            )
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
        if callable(getattr(self.storage, "search_core", None)):
            if not query.strip():
                raise InvalidTextSearchError("invalid_text_query")
            vector, degraded_reason = await self._prepare_query_vector(query)
            vector_branches: tuple[VectorBranch, ...] = ()
            if vector is not None:
                space_id = self._resolve_embedding_space()
                if space_id is None:
                    degraded_reason = "incompatible_embedding_profile"
                else:
                    vector_branches = (
                        VectorBranch(
                            "semantic",
                            space_id,
                            vector,
                            candidate_limit=limit * 2,
                        ),
                    )
            request = SearchRequest(
                lexical_branches=(
                    LexicalBranch("text", query, candidate_limit=limit * 2),
                ),
                vector_branches=vector_branches,
                scope=SearchScope(),
                target=TARGET_UNIT,
                limit=limit,
                rrf_k=self.rrf_k,
                allow_partial=False,
            )
            try:
                core = self.storage.search_core(request)
            except (BranchExecutionError, ValueError) as error:
                if not isinstance(error, BranchExecutionError) or error.branch_id == "text":
                    raise InvalidTextSearchError("invalid_text_query") from None
                degraded_reason = error.category.value
                core = self.storage.search_core(
                    SearchRequest(
                        lexical_branches=request.lexical_branches,
                        vector_branches=(),
                        scope=request.scope,
                        target=request.target,
                        limit=request.limit,
                        rrf_k=request.rrf_k,
                        allow_partial=False,
                    )
                )
            return self.compatibility_mapper.retrieval_result(
                query=query,
                mode="hybrid",
                result=core,
                degraded_reason=degraded_reason,
            )
        candidate_limit = limit * 2
        text_candidates = self.storage.retrieve_text_candidates(query, limit=candidate_limit, offset=0)
        semantic_candidates, degraded_reason = await self._semantic_candidates(query, limit=candidate_limit)
        return _fuse_with_core(
            query=query,
            text_candidates=text_candidates,
            semantic_candidates=semantic_candidates,
            limit=limit,
            rrf_k=self.rrf_k,
            mapper=self.compatibility_mapper,
            degraded_reason=degraded_reason,
        )

    async def _semantic_candidates(
        self,
        query: str,
        *,
        limit: int,
    ) -> tuple[list[RetrievalCandidate], str | None]:
        query_vector, degraded_reason = await self._prepare_query_vector(query)
        if query_vector is None:
            return [], degraded_reason
        try:
            candidates = self.storage.retrieve_semantic_candidates(
                list(query_vector),
                profile=self.profile,
                profile_fingerprint=self.profile_fingerprint,
                limit=limit,
            )
        except IncompatibleEmbeddingProfileError:
            logger.warning("retrieval.semantic.degraded reason=incompatible_embedding_profile")
            return [], "incompatible_embedding_profile"
        except Exception:
            logger.warning("retrieval.semantic.degraded reason=semantic_search_error")
            return [], "semantic_search_error"
        return candidates, None

    async def _prepare_query_vector(
        self,
        query: str,
    ) -> tuple[tuple[float, ...] | None, str | None]:
        if self.embedding_provider is None:
            logger.warning("retrieval.semantic.degraded reason=embedding_provider_unavailable")
            return None, "embedding_provider_unavailable"
        try:
            vector = await self.embedding_provider.embed_query(query, profile=self.profile)
        except EmbeddingError:
            logger.warning("retrieval.semantic.degraded reason=embedding_provider_error")
            return None, "embedding_provider_error"
        except Exception:
            logger.warning("retrieval.semantic.degraded reason=semantic_search_error")
            return None, "semantic_search_error"
        return tuple(float(value) for value in vector), None

    def _resolve_embedding_space(self) -> str | None:
        resolver = getattr(self.storage, "resolve_embedding_space", None)
        if not callable(resolver):
            return None
        value = resolver(self.profile, self.profile_fingerprint)
        return value if isinstance(value, str) and value else None

    @staticmethod
    def _empty_degraded(
        query: str,
        mode: RetrievalMode,
        reason: str | None,
    ) -> RetrievalResult:
        return RetrievalResult(
            query=query,
            mode=mode,
            results=(),
            total_count=0,
            degraded=True,
            degraded_reason=reason,
        )

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
        return _fuse_with_core(
            query=query,
            text_candidates=text_candidates,
            semantic_candidates=semantic_candidates,
            limit=limit,
            rrf_k=self.rrf_k,
            mapper=CoreCompatibilityMapper(),
        )
