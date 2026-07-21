"""Single application-level text, semantic, and hybrid retrieval path."""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, cast

from mdrack.application.compatibility import CoreCompatibilityMapper
from mdrack.application.metadata_filters import MetadataFilters, compile_metadata_filters
from mdrack.application.resources import (
    ResourcePresetEvidence,
    ResourcePresetSearchItem,
    ResourcePresetSearchResult,
)
from mdrack.domain.profiles import IncompatibleEmbeddingProfileError
from mdrack.domain.retrieval import (
    RetrievalCandidate,
    RetrievalItem,
    RetrievalMode,
    RetrievalResult,
)
from mdrack.ports.embeddings import EmbeddingError, EmbeddingProvider
from mdrack.ports.storage import RetrievalStorage
from mdrack_core.application.retrieval import RetrievalService as CoreRetrievalService
from mdrack_core.domain import (
    TARGET_RESOURCE,
    TARGET_UNIT,
    BranchExecutionError,
    BranchScopeOverride,
    CatalogExecutionError,
    EmbeddingSpaceRecord,
    ErrorCategory,
    LexicalBranch,
    RankedCandidate,
    SearchRequest,
    SearchScope,
    VectorBranch,
)

logger = logging.getLogger(__name__)

ResourceSearchMode = Literal["text", "semantic", "hybrid"]
ResourceSearchPresetName = Literal["speech_first", "balanced", "frames_first"]

_CATALOG_ERROR_TO_DEGRADED_REASON = {
    ErrorCategory.CATALOG_ERROR: "adapter_error",
    ErrorCategory.ADAPTER_TIMEOUT: "adapter_timeout",
}


def validate_embedding_vector(value: object) -> tuple[float, ...]:
    """Return one finite, non-empty provider vector or fail with the stable app error."""
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise EmbeddingError("invalid_embedding_vector")
    try:
        vector = tuple(float(item) for item in value)
    except (TypeError, ValueError):
        raise EmbeddingError("invalid_embedding_vector") from None
    if not vector or any(not math.isfinite(item) for item in vector):
        raise EmbeddingError("invalid_embedding_vector")
    return vector


@dataclass(frozen=True)
class ResourceSearchPreset:
    """Deterministic app-owned weights for text-first media retrieval."""

    transcript_weight: float
    frame_caption_weight: float
    metadata_weight: float
    lexical_fraction: float = 0.4
    semantic_fraction: float = 0.6


SEARCH_PRESETS: dict[str, ResourceSearchPreset] = {
    "speech_first": ResourceSearchPreset(1.0, 0.35, 0.15),
    "balanced": ResourceSearchPreset(1.0, 1.0, 0.20),
    "frames_first": ResourceSearchPreset(0.6, 1.0, 0.15),
}


def build_resource_search_request(
    query: str,
    *,
    preset: str,
    mode: ResourceSearchMode,
    query_vector: tuple[float, ...] | None = None,
    space_id: str | None = None,
    expected_fingerprint: str | None = None,
    scope: SearchScope | None = None,
    limit: int = 20,
    rrf_k: int = 60,
) -> SearchRequest:
    """Build the five bounded v1.1 branches without owning fusion or grouping."""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")
    if preset not in SEARCH_PRESETS:
        raise ValueError("preset must be speech_first, balanced, or frames_first")
    if mode not in {"text", "semantic", "hybrid"}:
        raise ValueError("mode must be text, semantic, or hybrid")
    if type(limit) is not int or limit < 1:
        raise ValueError("limit must be a positive integer")
    if type(rrf_k) is not int or rrf_k < 1:
        raise ValueError("rrf_k must be a positive integer")
    if mode in {"semantic", "hybrid"} and (
        query_vector is None or space_id is None or expected_fingerprint is None
    ):
        raise ValueError("semantic branches require a vector, space_id, and fingerprint")

    weights = SEARCH_PRESETS[preset]
    candidate_limit = max(100, limit * 10)
    lexical: list[LexicalBranch] = []
    vectors: list[VectorBranch] = []

    def branch_weight(signal_weight: float, fraction: float) -> float:
        return round(signal_weight * fraction, 12)

    if mode in {"text", "hybrid"}:
        lexical.extend(
            (
                LexicalBranch(
                    "transcript_text",
                    query,
                    weight=branch_weight(weights.transcript_weight, weights.lexical_fraction),
                    candidate_limit=candidate_limit,
                    scope_override=BranchScopeOverride(
                        representation_kinds=("timed_passage",),
                        unit_kinds=("time_segment",),
                    ),
                ),
                LexicalBranch(
                    "frame_caption_text",
                    query,
                    weight=branch_weight(weights.frame_caption_weight, weights.lexical_fraction),
                    candidate_limit=candidate_limit,
                    scope_override=BranchScopeOverride(
                        representation_kinds=("frame_caption",),
                        unit_kinds=("frame",),
                    ),
                ),
                LexicalBranch(
                    "metadata_text",
                    query,
                    weight=weights.metadata_weight,
                    candidate_limit=candidate_limit,
                    scope_override=BranchScopeOverride(
                        representation_kinds=("metadata_text",),
                        unit_kinds=("whole_resource",),
                    ),
                ),
            )
        )
    if mode in {"semantic", "hybrid"}:
        assert query_vector is not None
        assert space_id is not None
        assert expected_fingerprint is not None
        vectors.extend(
            (
                VectorBranch(
                    "transcript_semantic",
                    space_id,
                    query_vector,
                    weight=branch_weight(weights.transcript_weight, weights.semantic_fraction),
                    candidate_limit=candidate_limit,
                    expected_fingerprint=expected_fingerprint,
                    scope_override=BranchScopeOverride(
                        representation_kinds=("timed_passage",),
                        unit_kinds=("time_segment",),
                    ),
                ),
                VectorBranch(
                    "frame_caption_semantic",
                    space_id,
                    query_vector,
                    weight=branch_weight(weights.frame_caption_weight, weights.semantic_fraction),
                    candidate_limit=candidate_limit,
                    expected_fingerprint=expected_fingerprint,
                    scope_override=BranchScopeOverride(
                        representation_kinds=("frame_caption",),
                        unit_kinds=("frame",),
                    ),
                ),
            )
        )
    return SearchRequest(
        lexical_branches=tuple(lexical),
        vector_branches=tuple(vectors),
        scope=scope or SearchScope(),
        target=TARGET_RESOURCE,
        limit=limit,
        rrf_k=rrf_k,
        allow_partial=True,
    )


class ResourcePresetSearchService:
    """Prepare app-side query vectors, then delegate grouping and RRF to core."""

    def __init__(
        self,
        catalog: object,
        *,
        embedding_provider: EmbeddingProvider | None = None,
        embedding_fingerprint: str | None = None,
        profile: str = "default",
        rrf_k: int = 60,
    ) -> None:
        if not callable(getattr(catalog, "search_lexical", None)):
            raise TypeError("catalog must support lexical search")
        if not callable(getattr(catalog, "search_vector", None)):
            raise TypeError("catalog must support vector search")
        self._catalog = catalog
        self._provider = embedding_provider
        self._fingerprint = embedding_fingerprint
        self._profile = profile
        self._rrf_k = rrf_k

    async def search(
        self,
        query: str,
        *,
        preset: ResourceSearchPresetName = "balanced",
        mode: ResourceSearchMode = "hybrid",
        scope: SearchScope | None = None,
        limit: int = 20,
    ) -> ResourcePresetSearchResult:
        vector: tuple[float, ...] | None = None
        space_id: str | None = None
        degraded_reason: str | None = None
        effective_mode = mode
        if mode in {"semantic", "hybrid"}:
            if self._provider is None or self._fingerprint is None:
                degraded_reason = "embedding_provider_unavailable"
            else:
                try:
                    vector = validate_embedding_vector(
                        await self._provider.embed_query(query, profile=self._profile)
                    )
                except EmbeddingError:
                    degraded_reason = "embedding_provider_error"
                except Exception:
                    degraded_reason = "semantic_search_error"
                if vector:
                    resolver = getattr(self._catalog, "resolve_embedding_space", None)
                    try:
                        space = (
                            resolver(fingerprint=self._fingerprint, dimensions=len(vector))
                            if callable(resolver)
                            else None
                        )
                    except CatalogExecutionError as error:
                        degraded_reason = _CATALOG_ERROR_TO_DEGRADED_REASON[error.category]
                        space = None
                    except TimeoutError:
                        degraded_reason = "adapter_timeout"
                        space = None
                    except Exception:
                        degraded_reason = "adapter_error"
                        space = None
                    if space is None:
                        degraded_reason = degraded_reason or "incompatible_embedding_profile"
                        vector = None
                    elif not isinstance(space, EmbeddingSpaceRecord):
                        degraded_reason = "incompatible_embedding_profile"
                        vector = None
                    else:
                        resolved_space = cast(EmbeddingSpaceRecord, space)
                        if (
                            resolved_space.fingerprint != self._fingerprint
                            or resolved_space.dimensions != len(vector)
                        ):
                            degraded_reason = "incompatible_embedding_profile"
                            vector = None
                        else:
                            space_id = resolved_space.space_id
        if vector is None and mode == "semantic":
            return ResourcePresetSearchResult(
                query,
                mode,
                preset,
                (),
                degraded=True,
                degraded_reason=degraded_reason or "branch_unavailable",
            )
        if vector is None and mode == "hybrid":
            effective_mode = "text"
        request = build_resource_search_request(
            query,
            preset=preset,
            mode=effective_mode,
            query_vector=vector,
            space_id=space_id,
            expected_fingerprint=self._fingerprint if vector is not None else None,
            scope=scope,
            limit=limit,
            rrf_k=self._rrf_k,
        )
        try:
            result = CoreRetrievalService(self._catalog).search(request)  # type: ignore[arg-type]
        except BranchExecutionError as error:
            degraded_reason = error.category.value
            if effective_mode == "semantic":
                return ResourcePresetSearchResult(
                    query,
                    mode,
                    preset,
                    (),
                    degraded=True,
                    degraded_reason=degraded_reason,
                )
            lexical_request = build_resource_search_request(
                query,
                preset=preset,
                mode="text",
                scope=scope,
                limit=limit,
                rrf_k=self._rrf_k,
            )
            try:
                result = CoreRetrievalService(self._catalog).search(lexical_request)  # type: ignore[arg-type]
            except BranchExecutionError as lexical_error:
                return ResourcePresetSearchResult(
                    query,
                    mode,
                    preset,
                    (),
                    degraded=True,
                    degraded_reason=lexical_error.category.value,
                )
        reason = degraded_reason or (
            result.degradations[0].category.value if result.degradations else None
        )
        return ResourcePresetSearchResult(
            query,
            mode,
            preset,
            tuple(
                ResourcePresetSearchItem(
                    item.resource_id,
                    item.score,
                    item.rank,
                    tuple(
                        ResourcePresetEvidence.from_candidate(candidate)
                        for candidate in item.evidence
                    ),
                )
                for item in result.items
            ),
            degraded=reason is not None,
            degraded_reason=reason,
        )


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
        ranked: list[RankedCandidate] = []
        seen: set[str] = set()
        for candidate in candidates:
            if candidate.logical_id in seen:
                continue
            seen.add(candidate.logical_id)
            ranked.append(
                RankedCandidate(
                    unit_id=candidate.logical_id,
                    resource_id=str(candidate.metadata.get("resource_id") or candidate.logical_id),
                    representation_id=str(
                        candidate.metadata.get("representation_id") or candidate.logical_id
                    ),
                    rank=len(ranked) + 1,
                    raw_score=candidate.score,
                    branch_id=branch_id,
                    evidence_locator=mapper.core_locator(candidate.source_locator),
                    metadata={
                        "content_preview": candidate.content_preview,
                        "heading_path": candidate.source_locator.heading_path,
                        "section_title": candidate.metadata.get("section_title"),
                    },
                )
            )
        return ranked


def _fuse_with_core(
    *,
    query: str,
    text_candidates: list[RetrievalCandidate],
    semantic_candidates: list[RetrievalCandidate],
    limit: int,
    rrf_k: int,
    mapper: CoreCompatibilityMapper,
    text_weight: float = 1.0,
    semantic_weight: float = 1.0,
    degraded_reason: str | None = None,
) -> RetrievalResult:
    candidate_limit = max(limit * 2, len(text_candidates), len(semantic_candidates), 1)
    lexical_branches = (
        (
            LexicalBranch(
                "text",
                query or "compatibility-query",
                weight=text_weight,
                candidate_limit=candidate_limit,
            ),
        )
        if text_weight > 0.0
        else ()
    )
    vector_branches = (
        (
            VectorBranch(
                "semantic",
                "compatibility-space",
                (0.0,),
                weight=semantic_weight,
                candidate_limit=candidate_limit,
            ),
        )
        if semantic_weight > 0.0
        else ()
    )
    core = CoreRetrievalService(
        _CandidateSearchPort(text_candidates, semantic_candidates, mapper)
    ).search(
        SearchRequest(
            lexical_branches=lexical_branches,
            vector_branches=vector_branches,
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
        text_weight: float = 1.0,
        semantic_weight: float = 1.0,
    ) -> None:
        if rrf_k < 1:
            raise ValueError("rrf_k must be positive")
        for value in (text_weight, semantic_weight):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("search weights must be finite non-negative numbers")
            if not math.isfinite(float(value)) or value < 0.0:
                raise ValueError("search weights must be finite non-negative numbers")
        if text_weight == 0.0 and semantic_weight == 0.0:
            raise ValueError("at least one search weight must be positive")
        self.storage = storage
        self.embedding_provider = embedding_provider
        self.profile = profile
        self.profile_fingerprint = profile_fingerprint
        self.rrf_k = rrf_k
        self.text_weight = float(text_weight)
        self.semantic_weight = float(semantic_weight)
        self.compatibility_mapper = CoreCompatibilityMapper()

    def search_text(
        self,
        query: str,
        *,
        limit: int = 20,
        offset: int = 0,
        metadata_filters: MetadataFilters | None = None,
    ) -> RetrievalResult:
        self._validate_page(limit, offset)
        scope = compile_metadata_filters(metadata_filters or MetadataFilters())
        search_core = getattr(self.storage, "search_core", None)
        if callable(search_core):
            try:
                core = self.storage.search_core(
                    SearchRequest(
                        lexical_branches=(
                            LexicalBranch(
                                "text",
                                query,
                                candidate_limit=limit + offset,
                                scope_override=BranchScopeOverride(
                                    representation_kinds=("retrieval_text",),
                                    unit_kinds=("text_chunk",),
                                ),
                            ),
                        ),
                        vector_branches=(),
                        scope=scope,
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
        if metadata_filters is not None:
            raise ValueError("metadata filters require an active resource-core generation")
        candidates = self.storage.retrieve_text_candidates(query, limit=limit, offset=offset)
        items = tuple(
            self._candidate_item(candidate, text_rank=rank)
            for rank, candidate in enumerate(candidates, start=offset + 1)
        )
        return RetrievalResult(query=query, mode="text", results=items, total_count=len(items))

    async def search_semantic(
        self,
        query: str,
        *,
        limit: int = 20,
        metadata_filters: MetadataFilters | None = None,
    ) -> RetrievalResult:
        self._validate_page(limit, 0)
        scope = compile_metadata_filters(metadata_filters or MetadataFilters())
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
                            VectorBranch(
                                "semantic",
                                space_id,
                                vector,
                                candidate_limit=limit,
                                expected_fingerprint=self.profile_fingerprint,
                            ),
                        ),
                        scope=scope,
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
        if metadata_filters is not None:
            raise ValueError("metadata filters require an active resource-core generation")
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
        metadata_filters: MetadataFilters | None = None,
    ) -> RetrievalResult:
        """Fuse text and semantic ranks with RRF; production reranking is deferred."""
        if reranker is not None:
            raise ValueError("reranking is not supported in v0.2")
        self._validate_page(limit, 0)
        scope = compile_metadata_filters(metadata_filters or MetadataFilters())
        if callable(getattr(self.storage, "search_core", None)):
            if not query.strip():
                raise InvalidTextSearchError("invalid_text_query")
            vector: tuple[float, ...] | None = None
            degraded_reason: str | None = None
            if self.semantic_weight > 0.0:
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
                            weight=self.semantic_weight,
                            candidate_limit=limit * 2,
                            expected_fingerprint=self.profile_fingerprint,
                        ),
                    )
            request = SearchRequest(
                lexical_branches=(
                    (
                        LexicalBranch(
                            "text",
                            query,
                            weight=self.text_weight,
                            candidate_limit=limit * 2,
                            scope_override=BranchScopeOverride(
                                representation_kinds=("retrieval_text",),
                                unit_kinds=("text_chunk",),
                            ),
                        ),
                    )
                    if self.text_weight > 0.0
                    else ()
                ),
                vector_branches=vector_branches,
                scope=scope,
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
                if not request.lexical_branches:
                    return self._empty_degraded(query, "hybrid", degraded_reason)
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
        if metadata_filters is not None:
            raise ValueError("metadata filters require an active resource-core generation")
        candidate_limit = limit * 2
        text_candidates = (
            self.storage.retrieve_text_candidates(query, limit=candidate_limit, offset=0)
            if self.text_weight > 0.0
            else []
        )
        if self.semantic_weight > 0.0:
            semantic_candidates, degraded_reason = await self._semantic_candidates(
                query,
                limit=candidate_limit,
            )
        else:
            semantic_candidates, degraded_reason = [], None
        return _fuse_with_core(
            query=query,
            text_candidates=text_candidates,
            semantic_candidates=semantic_candidates,
            limit=limit,
            rrf_k=self.rrf_k,
            mapper=self.compatibility_mapper,
            text_weight=self.text_weight,
            semantic_weight=self.semantic_weight,
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
        try:
            return validate_embedding_vector(vector), None
        except EmbeddingError:
            logger.warning("retrieval.semantic.degraded reason=embedding_provider_error")
            return None, "embedding_provider_error"

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
