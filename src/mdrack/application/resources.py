"""App-owned public resource filters, duplicate lookup, and similarity mapping."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import cast

from mdrack_core.application.retrieval import ResourceDiscoveryService
from mdrack_core.domain import (
    CatalogExecutionError,
    ErrorCategory,
    Facet,
    RankedCandidate,
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
        "markdown_retrieval_text",
        "textual_content",
        "transcript_text",
    }
)


def _intersect_required(current: tuple[str, ...], required: str) -> tuple[str, ...]:
    if not current:
        return (required,)
    return (required,) if required in current else ("__mdrack_no_match__",)


class _TextualSimilaritySearchPort:
    """Filter persisted textual identities before exposing a candidate budget to core."""

    def __init__(self, catalog: object) -> None:
        self._catalog = cast(VectorSearchPort, catalog)

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
                if candidate.metadata.get("similarity_basis") in _TEXTUAL_SOURCE_BASES
                and candidate.metadata.get("aggregation") in _TEXTUAL_AGGREGATIONS
            ]
            raw_ids = tuple(candidate.unit_id for candidate in raw)
            if (
                len(selected) >= candidate_limit
                or len(raw) < fetch_limit
                or raw_ids == previous_ids
            ):
                break
            previous_ids = raw_ids
            fetch_limit *= 2
        return [
            replace(candidate, rank=rank)
            for rank, candidate in enumerate(selected[:candidate_limit], start=1)
        ]


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
            if not isinstance(values, (list, tuple)) or any(
                not isinstance(item, FacetFilter) for item in values
            ):
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


class ResourceQueryService:
    """Expose one logical-ID-only app path over frozen core catalog/search ports."""

    def __init__(self, catalog: ResourceReadPort) -> None:
        self._catalog = catalog
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
    ) -> TextualSimilarityResult:
        """Search explicit whole-resource text vectors through the core owner."""
        if aggregation not in _TEXTUAL_AGGREGATIONS:
            raise ValueError(
                "aggregation must be direct_text_v1 or token_weighted_centroid_v1"
            )
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
            or unit.metadata.get("similarity_basis") not in _TEXTUAL_SOURCE_BASES
            or stored_aggregation not in _TEXTUAL_AGGREGATIONS
            or stored_aggregation != aggregation
        ):
            return self._textual_unavailable(
                query_unit_id,
                space_id,
                "textual_similarity_identity_unavailable",
                query_resource_id=unit.resource_id,
            )
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
        if resolved is None or getattr(resolved, "space_id", None) != space_id:
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
            _TextualSimilaritySearchPort(self._catalog),
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
