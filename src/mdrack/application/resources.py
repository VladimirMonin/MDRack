"""App-owned public resource filters, duplicate lookup, and similarity mapping."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Literal, Protocol, cast

from mdrack.ports.embeddings import EmbeddingError, EmbeddingProvider
from mdrack_core.application.retrieval import ResourceDiscoveryService
from mdrack_core.application.retrieval import RetrievalService as CoreRetrievalService
from mdrack_core.domain import (
    TARGET_RESOURCE,
    BranchExecutionError,
    CatalogExecutionError,
    EmbeddingSpaceRecord,
    ErrorCategory,
    Facet,
    LexicalBranch,
    Locator,
    RankedCandidate,
    SearchRequest,
    SearchResult,
    SearchScope,
    SimilarityRequest,
    VectorBranch,
)
from mdrack_core.ports.catalog import ResourceReadPort
from mdrack_core.ports.search import VectorSearchPort

_CATALOG_ERROR_TO_DEGRADED_REASON = {
    ErrorCategory.CATALOG_ERROR: "adapter_error",
    ErrorCategory.ADAPTER_TIMEOUT: "adapter_timeout",
}
_LEGACY_SIMILARITY_BASIS = "legacy_unspecified"
_TEXTUAL_SIMILARITY_BASIS = "textual_content"
_TEXTUAL_AGGREGATIONS = frozenset({"direct_text_v1", "token_weighted_centroid_v1"})
_TEXTUAL_SOURCE_BASES = frozenset(
    {
        "frame_caption_text",
        "image_text_aggregate",
        "markdown_retrieval_text",
        "textual_content",
        "transcript_text",
    }
)
_UNIFIED_TEXTUAL_SOURCE_BASES = _TEXTUAL_SOURCE_BASES - {"frame_caption_text"}
UnifiedTextScopeName = Literal["all", "notes", "audio", "video", "frames", "images"]
UnifiedTextSearchMode = Literal["text", "semantic", "hybrid"]


def _intersect_required(current: tuple[str, ...], required: str) -> tuple[str, ...]:
    if not current:
        return (required,)
    return (required,) if required in current else ("__mdrack_no_match__",)


class _TextualSimilaritySearchPort:
    """Filter persisted textual identities before exposing a candidate budget to core."""

    def __init__(
        self,
        catalog: object,
        *,
        source_bases: frozenset[str] = _TEXTUAL_SOURCE_BASES,
    ) -> None:
        self._catalog = cast(VectorSearchPort, catalog)
        self._source_bases = source_bases

    def search_vector(
        self,
        branch: VectorBranch,
        *,
        scope: SearchScope,
    ) -> list[RankedCandidate]:
        candidate_limit = branch.candidate_limit
        fetch_limit = candidate_limit
        previous_ids: tuple[str, ...] | None = None
        selected: list[RankedCandidate] = []
        while True:
            raw = self._catalog.search_vector(
                replace(branch, candidate_limit=fetch_limit),
                scope=scope,
            )
            selected = [
                candidate
                for candidate in raw
                if candidate.metadata.get("similarity_basis") in self._source_bases
                and candidate.metadata.get("aggregation") in _TEXTUAL_AGGREGATIONS
            ]
            raw_ids = tuple(candidate.unit_id for candidate in raw)
            if len(selected) >= candidate_limit or len(raw) < fetch_limit or raw_ids == previous_ids:
                break
            previous_ids = raw_ids
            fetch_limit *= 2
        return [replace(candidate, rank=rank) for rank, candidate in enumerate(selected[:candidate_limit], start=1)]


@dataclass(frozen=True)
class FacetFilter:
    namespace: str
    value: str

    def __post_init__(self) -> None:
        Facet(self.namespace, self.value)

    def core(self) -> Facet:
        return Facet(self.namespace, self.value)


@dataclass(frozen=True)
class ResourceQueryScope:
    resource_kinds: tuple[str, ...] = ()
    media_types: tuple[str, ...] = ()
    source_namespaces: tuple[str, ...] = ()
    representation_kinds: tuple[str, ...] = ()
    modalities: tuple[str, ...] = ()
    unit_kinds: tuple[str, ...] = ()
    facets_any: tuple[FacetFilter, ...] = ()
    facets_all: tuple[FacetFilter, ...] = ()
    facets_none: tuple[FacetFilter, ...] = ()

    def __post_init__(self) -> None:
        for field_name in ("facets_any", "facets_all", "facets_none"):
            values = getattr(self, field_name)
            if not isinstance(values, (list, tuple)) or any(not isinstance(item, FacetFilter) for item in values):
                raise ValueError(f"{field_name} must contain FacetFilter values")
            object.__setattr__(self, field_name, tuple(values))
        core = self.core()
        for field_name in (
            "resource_kinds",
            "media_types",
            "source_namespaces",
            "representation_kinds",
            "modalities",
            "unit_kinds",
        ):
            object.__setattr__(self, field_name, getattr(core, field_name))

    def core(self) -> SearchScope:
        return SearchScope(
            resource_kinds=self.resource_kinds,
            media_types=self.media_types,
            source_namespaces=self.source_namespaces,
            representation_kinds=self.representation_kinds,
            modalities=self.modalities,
            unit_kinds=self.unit_kinds,
            facets_any=tuple(item.core() for item in self.facets_any),
            facets_all=tuple(item.core() for item in self.facets_all),
            facets_none=tuple(item.core() for item in self.facets_none),
        )


_UNIFIED_TEXT_SCOPES: dict[UnifiedTextScopeName, ResourceQueryScope] = {
    "all": ResourceQueryScope(modalities=("text",)),
    "notes": ResourceQueryScope(resource_kinds=("document",), modalities=("text",)),
    "audio": ResourceQueryScope(resource_kinds=("audio",), modalities=("text",)),
    "video": ResourceQueryScope(
        resource_kinds=("video",),
        representation_kinds=("timed_passage",),
        modalities=("text",),
        unit_kinds=("time_segment",),
    ),
    "frames": ResourceQueryScope(
        resource_kinds=("video",),
        representation_kinds=("frame_caption",),
        modalities=("text",),
        unit_kinds=("frame",),
    ),
    "images": ResourceQueryScope(resource_kinds=("image",), modalities=("text",)),
}


def resolve_unified_text_scope(scope: UnifiedTextScopeName) -> ResourceQueryScope:
    """Compile a public unified text alias to existing typed core scope fields."""
    try:
        return _UNIFIED_TEXT_SCOPES[scope]
    except KeyError:
        raise ValueError("scope must be all, notes, audio, video, frames, or images") from None


def _resolve_unified_text_similarity_scope(scope: UnifiedTextScopeName) -> ResourceQueryScope:
    """Keep resource-kind aliases while selecting textual whole-resource candidates."""
    return replace(
        resolve_unified_text_scope(scope),
        representation_kinds=(),
        unit_kinds=("whole_resource",),
    )


def _portable_relative_path(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    if value.startswith(("/", "\\")) or "\\" in value or ":" in value:
        return None
    if any(part in {"", ".", ".."} for part in value.split("/")):
        return None
    return value


def _portable_evidence_locator(locator: Locator) -> dict[str, object]:
    """Expose only portable coordinates, never adapter-specific source references."""
    payload: Mapping[str, object] = locator.payload
    safe_payload: dict[str, object] = {}
    if locator.kind in {"document_span", "whole_resource"}:
        relative_path = _portable_relative_path(payload.get("relative_path"))
        if relative_path is not None:
            safe_payload["relative_path"] = relative_path
        for field_name in ("start_line", "end_line", "start_char", "end_char"):
            value = payload.get(field_name)
            if type(value) is int and value >= 0:
                safe_payload[field_name] = value
        block_kind = payload.get("block_kind")
        if isinstance(block_kind, str) and block_kind:
            safe_payload["block_kind"] = block_kind
        return {"kind": locator.kind, "payload": safe_payload}
    if locator.kind == "time_segment":
        for field_name in ("start_ms", "end_ms"):
            value = payload.get(field_name)
            if type(value) is int and value >= 0:
                safe_payload[field_name] = value
        track = payload.get("track")
        if track in {"audio", "video"}:
            safe_payload["track"] = track
        return {"kind": locator.kind, "payload": safe_payload}
    if locator.kind == "video_frame":
        timestamp = payload.get("timestamp_ms")
        if type(timestamp) is int and timestamp >= 0:
            safe_payload["timestamp_ms"] = timestamp
        return {"kind": locator.kind, "payload": safe_payload}
    if locator.kind in {"whole_image", "whole_media"}:
        return {"kind": locator.kind, "payload": safe_payload}
    return {"kind": "opaque", "payload": safe_payload}


@dataclass(frozen=True)
class TextualWholeResourceProjection:
    """One adapter-resolved whole-resource vector identity without SQLite row IDs."""

    resource_id: str
    unit_id: str
    space: EmbeddingSpaceRecord


class WholeResourceTextResolver(Protocol):
    def resolve_textual_whole_resource_units(
        self,
        resource_id: str,
    ) -> tuple[TextualWholeResourceProjection, ...]: ...


@dataclass(frozen=True)
class DuplicateResourceItem:
    resource_id: str

    def to_dict(self) -> dict[str, str]:
        return {"resource_id": self.resource_id}


@dataclass(frozen=True)
class DuplicateResourceResult:
    query_resource_id: str
    results: tuple[DuplicateResourceItem, ...]
    degraded: bool = False
    degraded_reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "query_resource_id": self.query_resource_id,
            "results": [item.to_dict() for item in self.results],
            "total_count": len(self.results),
            "degraded": self.degraded,
            "degraded_reason": self.degraded_reason,
        }


@dataclass(frozen=True)
class SimilarResourceItem:
    resource_id: str
    unit_id: str
    score: float
    rank: int

    def to_dict(self) -> dict[str, object]:
        return {
            "resource_id": self.resource_id,
            "unit_id": self.unit_id,
            "score": self.score,
            "rank": self.rank,
        }


@dataclass(frozen=True)
class SimilarResourceResult:
    query_unit_id: str
    space_id: str
    results: tuple[SimilarResourceItem, ...]
    degraded: bool = False
    degraded_reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "query_unit_id": self.query_unit_id,
            "space_id": self.space_id,
            "results": [item.to_dict() for item in self.results],
            "total_count": len(self.results),
            "degraded": self.degraded,
            "degraded_reason": self.degraded_reason,
        }


@dataclass(frozen=True)
class ResourcePresetEvidence:
    branch_id: str
    unit_id: str
    representation_id: str
    locator: dict[str, object]

    @classmethod
    def from_candidate(cls, candidate: RankedCandidate) -> ResourcePresetEvidence:
        return cls(
            candidate.branch_id,
            candidate.unit_id,
            candidate.representation_id,
            {
                "kind": candidate.evidence_locator.kind,
                "payload": dict(candidate.evidence_locator.payload),
            },
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "branch_id": self.branch_id,
            "unit_id": self.unit_id,
            "representation_id": self.representation_id,
            "locator": dict(self.locator),
        }


@dataclass(frozen=True)
class ResourcePresetSearchItem:
    resource_id: str
    score: float
    rank: int
    evidence: tuple[ResourcePresetEvidence, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "resource_id": self.resource_id,
            "score": self.score,
            "rank": self.rank,
            "evidence": [item.to_dict() for item in self.evidence],
        }


@dataclass(frozen=True)
class ResourcePresetSearchResult:
    query: str
    mode: str
    preset: str
    results: tuple[ResourcePresetSearchItem, ...]
    degraded: bool = False
    degraded_reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "query": self.query,
            "mode": self.mode,
            "preset": self.preset,
            "target": "resource",
            "results": [item.to_dict() for item in self.results],
            "total_count": len(self.results),
            "degraded": self.degraded,
            "degraded_reason": self.degraded_reason,
        }


@dataclass(frozen=True)
class TextualSimilarResourceItem:
    resource_id: str
    unit_id: str
    score: float
    rank: int
    evidence: tuple[ResourcePresetEvidence, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "resource_id": self.resource_id,
            "unit_id": self.unit_id,
            "score": self.score,
            "rank": self.rank,
            "evidence": [item.to_dict() for item in self.evidence],
        }


@dataclass(frozen=True)
class TextualSimilarityResult:
    query_resource_id: str | None
    query_unit_id: str
    space_id: str
    similarity_basis: str
    aggregation: str | None
    results: tuple[TextualSimilarResourceItem, ...]
    degraded: bool = False
    degraded_reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "query_resource_id": self.query_resource_id,
            "query_unit_id": self.query_unit_id,
            "space_id": self.space_id,
            "similarity_basis": self.similarity_basis,
            "aggregation": self.aggregation,
            "results": [item.to_dict() for item in self.results],
            "total_count": len(self.results),
            "degraded": self.degraded,
            "degraded_reason": self.degraded_reason,
        }


@dataclass(frozen=True)
class UnifiedTextEvidence:
    branch_id: str
    unit_id: str
    representation_id: str
    unit_kind: str
    locator: dict[str, object]

    @classmethod
    def from_candidate(cls, candidate: RankedCandidate, *, unit_kind: str) -> UnifiedTextEvidence:
        return cls(
            candidate.branch_id,
            candidate.unit_id,
            candidate.representation_id,
            unit_kind,
            _portable_evidence_locator(candidate.evidence_locator),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "branch_id": self.branch_id,
            "unit_id": self.unit_id,
            "representation_id": self.representation_id,
            "unit_kind": self.unit_kind,
            "locator": dict(self.locator),
        }


@dataclass(frozen=True)
class UnifiedTextSearchItem:
    resource_id: str
    resource_kind: str
    score: float
    rank: int
    evidence: tuple[UnifiedTextEvidence, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "resource_id": self.resource_id,
            "resource_kind": self.resource_kind,
            "score": self.score,
            "rank": self.rank,
            "evidence": [item.to_dict() for item in self.evidence],
        }


@dataclass(frozen=True)
class UnifiedTextSearchResult:
    query: str
    mode: UnifiedTextSearchMode
    scope: UnifiedTextScopeName
    results: tuple[UnifiedTextSearchItem, ...]
    degraded: bool = False
    degraded_reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "query": self.query,
            "mode": self.mode,
            "scope": self.scope,
            "target": "resource",
            "results": [item.to_dict() for item in self.results],
            "total_count": len(self.results),
            "degraded": self.degraded,
            "degraded_reason": self.degraded_reason,
        }


class UnifiedTextSearchService:
    """Delegate one typed all|notes|audio|video|frames|images request to core ranking."""

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
        scope: UnifiedTextScopeName = "all",
        mode: UnifiedTextSearchMode = "hybrid",
        limit: int = 20,
    ) -> UnifiedTextSearchResult:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string")
        if mode not in {"text", "semantic", "hybrid"}:
            raise ValueError("mode must be text, semantic, or hybrid")
        if type(limit) is not int or limit < 1:
            raise ValueError("limit must be a positive integer")
        resolved_scope = resolve_unified_text_scope(scope)
        vector, spaces, reason = await self._semantic_query_vector(query, mode)
        if mode == "semantic" and vector is None:
            return UnifiedTextSearchResult(query, mode, scope, (), True, reason or "branch_unavailable")
        effective_mode: UnifiedTextSearchMode = "text" if vector is None and mode == "hybrid" else mode
        request = self._request(
            query,
            mode=effective_mode,
            scope=resolved_scope.core(),
            limit=limit,
            vector=vector,
            spaces=spaces,
        )
        try:
            result = CoreRetrievalService(self._catalog).search(request)  # type: ignore[arg-type]
        except BranchExecutionError as error:
            if effective_mode == "text":
                return UnifiedTextSearchResult(query, mode, scope, (), True, error.category.value)
            reason = error.category.value
            request = self._request(
                query,
                mode="text",
                scope=resolved_scope.core(),
                limit=limit,
                vector=None,
                spaces=(),
            )
            try:
                result = CoreRetrievalService(self._catalog).search(request)  # type: ignore[arg-type]
            except BranchExecutionError as lexical_error:
                return UnifiedTextSearchResult(query, mode, scope, (), True, lexical_error.category.value)
        mapped = self._map_result(result, mode=effective_mode)
        core_reason = result.degradations[0].category.value if result.degradations else None
        return UnifiedTextSearchResult(
            query,
            mode,
            scope,
            mapped,
            degraded=reason is not None or core_reason is not None,
            degraded_reason=reason or core_reason,
        )

    async def _semantic_query_vector(
        self,
        query: str,
        mode: UnifiedTextSearchMode,
    ) -> tuple[tuple[float, ...] | None, tuple[EmbeddingSpaceRecord, ...], str | None]:
        if mode == "text":
            return None, (), None
        if self._provider is None or self._fingerprint is None:
            return None, (), "embedding_provider_unavailable"
        try:
            raw_vector = await self._provider.embed_query(query, profile=self._profile)
            vector = self._validated_query_vector(raw_vector)
        except EmbeddingError:
            return None, (), "embedding_provider_error"
        except Exception:
            return None, (), "semantic_search_error"
        plural_resolver = getattr(self._catalog, "resolve_embedding_spaces", None)
        singular_resolver = getattr(self._catalog, "resolve_embedding_space", None)
        try:
            if callable(plural_resolver):
                resolved = plural_resolver(
                    fingerprint=self._fingerprint,
                    dimensions=len(vector),
                )
            elif callable(singular_resolver):
                single = singular_resolver(
                    fingerprint=self._fingerprint,
                    dimensions=len(vector),
                )
                resolved = () if single is None else (single,)
            else:
                resolved = ()
        except CatalogExecutionError as error:
            return None, (), _CATALOG_ERROR_TO_DEGRADED_REASON[error.category]
        except TimeoutError:
            return None, (), "adapter_timeout"
        except Exception:
            return None, (), "adapter_error"
        if (
            not isinstance(resolved, Sequence)
            or not resolved
            or any(not isinstance(space, EmbeddingSpaceRecord) for space in resolved)
        ):
            return None, (), "incompatible_embedding_profile"
        spaces = tuple(cast(Sequence[EmbeddingSpaceRecord], resolved))
        fingerprint_variants = {self._fingerprint}
        raw_digest = self._fingerprint.removeprefix("sha256:")
        if len(raw_digest) == 64 and all(character in "0123456789abcdef" for character in raw_digest.lower()):
            fingerprint_variants.update({raw_digest, f"sha256:{raw_digest}"})
        if any(space.fingerprint not in fingerprint_variants or space.dimensions != len(vector) for space in spaces):
            return None, (), "incompatible_embedding_profile"
        unique_spaces = {space.space_id: space for space in spaces}
        return vector, tuple(unique_spaces.values()), None

    def _request(
        self,
        query: str,
        *,
        mode: UnifiedTextSearchMode,
        scope: SearchScope,
        limit: int,
        vector: tuple[float, ...] | None,
        spaces: Sequence[EmbeddingSpaceRecord],
    ) -> SearchRequest:
        candidate_limit = max(100, limit * 10)
        lexical = (
            (LexicalBranch("unified_text", query, candidate_limit=candidate_limit),)
            if mode in {"text", "hybrid"}
            else ()
        )
        vectors = (
            tuple(
                VectorBranch(
                    "unified_semantic" if len(spaces) == 1 else f"unified_semantic_{index}",
                    space.space_id,
                    vector,
                    candidate_limit=candidate_limit,
                    expected_fingerprint=space.fingerprint,
                )
                for index, space in enumerate(spaces, start=1)
            )
            if mode in {"semantic", "hybrid"} and vector is not None and spaces
            else ()
        )
        return SearchRequest(lexical, vectors, scope, TARGET_RESOURCE, limit, self._rrf_k, allow_partial=True)

    def _map_result(
        self,
        result: SearchResult,
        *,
        mode: UnifiedTextSearchMode,
    ) -> tuple[UnifiedTextSearchItem, ...]:
        items: list[UnifiedTextSearchItem] = []
        for item in result.items:
            resource = cast(ResourceReadPort, self._catalog).read_resource(item.resource_id)
            if resource is None:
                continue
            evidence: list[UnifiedTextEvidence] = []
            for candidate in item.evidence:
                unit = cast(ResourceReadPort, self._catalog).read_unit(candidate.unit_id)
                if unit is not None:
                    evidence.append(UnifiedTextEvidence.from_candidate(candidate, unit_kind=unit.unit_kind))
            if not evidence:
                continue
            score = item.score if mode == "hybrid" else item.evidence[0].raw_score
            items.append(
                UnifiedTextSearchItem(
                    item.resource_id,
                    resource.resource_kind,
                    score,
                    item.rank,
                    tuple(evidence),
                )
            )
        return tuple(items)

    @staticmethod
    def _validated_query_vector(value: object) -> tuple[float, ...]:
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
            raise EmbeddingError("invalid_embedding_vector")
        try:
            vector = tuple(float(item) for item in value)
        except (TypeError, ValueError):
            raise EmbeddingError("invalid_embedding_vector") from None
        if not vector or any(not math.isfinite(item) for item in vector):
            raise EmbeddingError("invalid_embedding_vector")
        return vector


class ResourceQueryService:
    """Expose one logical-ID-only app path over frozen core catalog/search ports."""

    def __init__(
        self,
        catalog: ResourceReadPort,
        *,
        whole_resource_resolver: WholeResourceTextResolver | None = None,
    ) -> None:
        self._catalog = catalog
        self._whole_resource_resolver = whole_resource_resolver
        self._discovery = ResourceDiscoveryService(
            catalog,
            cast(VectorSearchPort, catalog),
        )

    def find_duplicates(
        self,
        resource_id: str,
        *,
        scope: ResourceQueryScope | None = None,
        limit: int = 20,
    ) -> DuplicateResourceResult:
        try:
            try:
                resource = self._catalog.read_resource(resource_id)
            except CatalogExecutionError:
                raise
            except TimeoutError:
                raise CatalogExecutionError(ErrorCategory.ADAPTER_TIMEOUT) from None
            except Exception:
                raise CatalogExecutionError(ErrorCategory.CATALOG_ERROR) from None
            if resource is None:
                return DuplicateResourceResult(resource_id, (), True, "resource_unavailable")
            if resource.content_hash is None:
                return DuplicateResourceResult(resource_id, (), True, "content_hash_unavailable")
            matches = self._discovery.find_duplicates(
                resource_id,
                scope=(scope or ResourceQueryScope()).core(),
                limit=limit,
            )
        except CatalogExecutionError as error:
            return DuplicateResourceResult(
                resource_id,
                (),
                True,
                _CATALOG_ERROR_TO_DEGRADED_REASON[error.category],
            )
        return DuplicateResourceResult(
            resource_id,
            tuple(DuplicateResourceItem(item.resource_id) for item in matches),
        )

    def find_similar(
        self,
        query_unit_id: str,
        space_id: str,
        *,
        scope: ResourceQueryScope | None = None,
        limit: int = 20,
        exclude_same_resource: bool = True,
    ) -> SimilarResourceResult:
        result = self._discovery.similar(
            SimilarityRequest(
                query_unit_id,
                space_id,
                _LEGACY_SIMILARITY_BASIS,
                (scope or ResourceQueryScope()).core(),
                limit,
                exclude_same_resource,
            )
        )
        reason = result.degradations[0].category.value if result.degradations else None
        return SimilarResourceResult(
            query_unit_id,
            space_id,
            tuple(
                SimilarResourceItem(
                    item.resource_id,
                    item.unit_id or item.evidence[0].unit_id,
                    item.score,
                    item.rank,
                )
                for item in result.items
            ),
            degraded=reason is not None,
            degraded_reason=reason,
        )

    def find_similar_resource(
        self,
        resource_id: str,
        *,
        scope: UnifiedTextScopeName = "all",
        limit: int = 20,
        exclude_same_resource: bool = True,
    ) -> TextualSimilarityResult:
        """Resolve one textual whole-resource identity without a provider call."""
        if scope == "frames":
            return self._textual_unavailable(
                "",
                "",
                "scope_not_similarity_compatible",
                query_resource_id=resource_id,
            )
        resolved_scope = _resolve_unified_text_similarity_scope(scope)
        try:
            resource = self._catalog.read_resource(resource_id)
        except CatalogExecutionError as error:
            return self._textual_unavailable(
                "",
                "",
                _CATALOG_ERROR_TO_DEGRADED_REASON[error.category],
                query_resource_id=resource_id,
            )
        except TimeoutError:
            return self._textual_unavailable("", "", "adapter_timeout", query_resource_id=resource_id)
        except Exception:
            return self._textual_unavailable("", "", "adapter_error", query_resource_id=resource_id)
        if resource is None:
            return self._textual_unavailable("", "", "resource_unavailable", query_resource_id=resource_id)
        if resolved_scope.resource_kinds and resource.resource_kind not in resolved_scope.resource_kinds:
            return self._textual_unavailable(
                "",
                "",
                "scope_excludes_query_resource",
                query_resource_id=resource_id,
            )
        if self._whole_resource_resolver is None:
            return self._textual_unavailable(
                "",
                "",
                "textual_similarity_identity_unavailable",
                query_resource_id=resource_id,
            )
        try:
            projections = self._whole_resource_resolver.resolve_textual_whole_resource_units(resource_id)
            candidates = tuple(
                projection for projection in projections if self._is_unified_textual_projection(projection, resource_id)
            )
        except CatalogExecutionError as error:
            return self._textual_unavailable(
                "",
                "",
                _CATALOG_ERROR_TO_DEGRADED_REASON[error.category],
                query_resource_id=resource_id,
            )
        except TimeoutError:
            return self._textual_unavailable("", "", "adapter_timeout", query_resource_id=resource_id)
        except Exception:
            return self._textual_unavailable("", "", "adapter_error", query_resource_id=resource_id)
        if not candidates:
            return self._textual_unavailable(
                "",
                "",
                "textual_similarity_identity_unavailable",
                query_resource_id=resource_id,
            )
        if len(candidates) != 1:
            return self._textual_unavailable(
                "",
                "",
                "textual_similarity_identity_ambiguous",
                query_resource_id=resource_id,
            )
        candidate = candidates[0]
        try:
            unit = self._catalog.read_unit(candidate.unit_id)
        except CatalogExecutionError as error:
            return self._textual_unavailable(
                "",
                "",
                _CATALOG_ERROR_TO_DEGRADED_REASON[error.category],
                query_resource_id=resource_id,
            )
        except TimeoutError:
            return self._textual_unavailable("", "", "adapter_timeout", query_resource_id=resource_id)
        except Exception:
            return self._textual_unavailable("", "", "adapter_error", query_resource_id=resource_id)
        if unit is None:
            return self._textual_unavailable(
                "",
                "",
                "textual_similarity_identity_unavailable",
                query_resource_id=resource_id,
            )
        aggregation = unit.metadata.get("aggregation")
        if not isinstance(aggregation, str):
            return self._textual_unavailable(
                "",
                "",
                "textual_similarity_identity_unavailable",
                query_resource_id=resource_id,
            )
        return self.find_textual_similarity(
            candidate.unit_id,
            candidate.space.space_id,
            aggregation=aggregation,
            expected_fingerprint=candidate.space.fingerprint,
            scope=resolved_scope,
            limit=limit,
            exclude_same_resource=exclude_same_resource,
            _resolved_space=candidate.space,
            _source_bases=_UNIFIED_TEXTUAL_SOURCE_BASES,
        )

    def _is_unified_textual_projection(
        self,
        projection: TextualWholeResourceProjection,
        resource_id: str,
    ) -> bool:
        if projection.resource_id != resource_id:
            return False
        unit = self._catalog.read_unit(projection.unit_id)
        if unit is None:
            return False
        return (
            unit.resource_id == resource_id
            and unit.unit_kind == "whole_resource"
            and unit.modality == "text"
            and unit.metadata.get("similarity_basis") in _UNIFIED_TEXTUAL_SOURCE_BASES
            and unit.metadata.get("aggregation") in _TEXTUAL_AGGREGATIONS
        )

    def find_textual_similarity(
        self,
        query_unit_id: str,
        space_id: str,
        *,
        aggregation: str,
        expected_fingerprint: str,
        scope: ResourceQueryScope | None = None,
        limit: int = 20,
        exclude_same_resource: bool = True,
        _resolved_space: EmbeddingSpaceRecord | None = None,
        _source_bases: frozenset[str] = _TEXTUAL_SOURCE_BASES,
    ) -> TextualSimilarityResult:
        """Search explicit whole-resource text vectors through the core owner."""
        if aggregation not in _TEXTUAL_AGGREGATIONS:
            raise ValueError("aggregation must be direct_text_v1 or token_weighted_centroid_v1")
        if not isinstance(expected_fingerprint, str) or not expected_fingerprint:
            raise ValueError("expected_fingerprint must be a non-empty string")
        try:
            unit = self._catalog.read_unit(query_unit_id)
            vector = self._catalog.read_vector(query_unit_id, space_id)
        except CatalogExecutionError as error:
            return self._textual_unavailable(
                query_unit_id,
                space_id,
                _CATALOG_ERROR_TO_DEGRADED_REASON[error.category],
            )
        except TimeoutError:
            return self._textual_unavailable(query_unit_id, space_id, "adapter_timeout")
        except Exception:
            return self._textual_unavailable(query_unit_id, space_id, "adapter_error")
        if unit is None or vector is None or unit.unit_kind != "whole_resource":
            return self._textual_unavailable(query_unit_id, space_id, "branch_unavailable")
        stored_aggregation = unit.metadata.get("aggregation")
        if (
            unit.modality != "text"
            or unit.metadata.get("similarity_basis") not in _source_bases
            or stored_aggregation not in _TEXTUAL_AGGREGATIONS
            or stored_aggregation != aggregation
        ):
            return self._textual_unavailable(
                query_unit_id,
                space_id,
                "textual_similarity_identity_unavailable",
                query_resource_id=unit.resource_id,
            )
        resolved = _resolved_space
        if resolved is None:
            resolver = getattr(self._catalog, "resolve_embedding_space", None)
            try:
                resolved = (
                    resolver(fingerprint=expected_fingerprint, dimensions=len(vector.vector))
                    if callable(resolver)
                    else None
                )
            except CatalogExecutionError as error:
                return self._textual_unavailable(
                    query_unit_id,
                    space_id,
                    _CATALOG_ERROR_TO_DEGRADED_REASON[error.category],
                    query_resource_id=unit.resource_id,
                    aggregation=aggregation,
                )
            except TimeoutError:
                return self._textual_unavailable(
                    query_unit_id,
                    space_id,
                    "adapter_timeout",
                    query_resource_id=unit.resource_id,
                    aggregation=aggregation,
                )
            except Exception:
                return self._textual_unavailable(
                    query_unit_id,
                    space_id,
                    "adapter_error",
                    query_resource_id=unit.resource_id,
                    aggregation=aggregation,
                )
        if not isinstance(resolved, EmbeddingSpaceRecord):
            return self._textual_unavailable(
                query_unit_id,
                space_id,
                "incompatible_vector_space",
                query_resource_id=unit.resource_id,
                aggregation=aggregation,
            )
        resolved_space = resolved
        if (
            resolved_space.space_id != space_id
            or resolved_space.fingerprint != expected_fingerprint
            or resolved_space.dimensions != len(vector.vector)
        ):
            return self._textual_unavailable(
                query_unit_id,
                space_id,
                "incompatible_vector_space",
                query_resource_id=unit.resource_id,
                aggregation=aggregation,
            )
        requested_scope = scope or ResourceQueryScope()
        textual_scope = replace(
            requested_scope,
            modalities=_intersect_required(requested_scope.modalities, "text"),
        )
        result = ResourceDiscoveryService(
            self._catalog,
            _TextualSimilaritySearchPort(self._catalog, source_bases=_source_bases),
        ).similar(
            SimilarityRequest(
                query_unit_id,
                space_id,
                _TEXTUAL_SIMILARITY_BASIS,
                textual_scope.core(),
                limit,
                exclude_same_resource,
            )
        )
        reason = result.degradations[0].category.value if result.degradations else None
        return TextualSimilarityResult(
            unit.resource_id,
            query_unit_id,
            space_id,
            _TEXTUAL_SIMILARITY_BASIS,
            aggregation,
            tuple(
                TextualSimilarResourceItem(
                    item.resource_id,
                    item.unit_id or item.evidence[0].unit_id,
                    item.score,
                    item.rank,
                    tuple(ResourcePresetEvidence.from_candidate(candidate) for candidate in item.evidence),
                )
                for item in result.items
            ),
            degraded=reason is not None,
            degraded_reason=reason,
        )

    @staticmethod
    def _textual_unavailable(
        query_unit_id: str,
        space_id: str,
        reason: str,
        *,
        query_resource_id: str | None = None,
        aggregation: str | None = None,
    ) -> TextualSimilarityResult:
        return TextualSimilarityResult(
            query_resource_id,
            query_unit_id,
            space_id,
            _TEXTUAL_SIMILARITY_BASIS,
            aggregation,
            (),
            degraded=True,
            degraded_reason=reason,
        )
