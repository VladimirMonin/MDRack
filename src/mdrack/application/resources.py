"""App-owned public resource filters, duplicate lookup, and similarity mapping."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from mdrack_core.application.retrieval import ResourceDiscoveryService
from mdrack_core.domain import (
    CatalogExecutionError,
    ErrorCategory,
    Facet,
    SearchScope,
    SimilarityRequest,
)
from mdrack_core.ports.catalog import ResourceReadPort
from mdrack_core.ports.search import VectorSearchPort

_CATALOG_ERROR_TO_DEGRADED_REASON = {
    ErrorCategory.CATALOG_ERROR: "adapter_error",
    ErrorCategory.ADAPTER_TIMEOUT: "adapter_timeout",
}


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
