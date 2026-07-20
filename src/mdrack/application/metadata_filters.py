"""Compile app-owned typed metadata filters into frozen core facet scopes."""

from __future__ import annotations

from dataclasses import dataclass

from mdrack.application.metadata_projection import (
    FACET_SCALAR_CODEC,
    MetadataProjectionPolicy,
    MetadataScalar,
)
from mdrack_core.domain import Facet, SearchScope


@dataclass(frozen=True)
class MetadataFilter:
    """One exact typed value in an explicitly projected facet namespace."""

    namespace: str
    value: MetadataScalar

    def __post_init__(self) -> None:
        Facet(self.namespace, FACET_SCALAR_CODEC.encode(self.value))

    def core(self) -> Facet:
        return Facet(self.namespace, FACET_SCALAR_CODEC.encode(self.value))


@dataclass(frozen=True)
class MetadataFilters:
    any: tuple[MetadataFilter, ...] = ()
    all: tuple[MetadataFilter, ...] = ()
    none: tuple[MetadataFilter, ...] = ()

    def __post_init__(self) -> None:
        for field_name in ("any", "all", "none"):
            values = getattr(self, field_name)
            if not isinstance(values, (tuple, list)) or any(
                not isinstance(item, MetadataFilter) for item in values
            ):
                raise ValueError(f"{field_name} must contain MetadataFilter values")
            object.__setattr__(self, field_name, tuple(values))


def compile_metadata_filters(
    filters: MetadataFilters,
    *,
    base_scope: SearchScope | None = None,
) -> SearchScope:
    """Compile typed values before adapters apply branch candidate limits."""

    if not isinstance(filters, MetadataFilters):
        raise TypeError("filters must be MetadataFilters")
    scope = base_scope or SearchScope()
    if not isinstance(scope, SearchScope):
        raise TypeError("base_scope must be SearchScope or None")
    return SearchScope(
        resource_kinds=scope.resource_kinds,
        media_types=scope.media_types,
        source_namespaces=scope.source_namespaces,
        representation_kinds=scope.representation_kinds,
        modalities=scope.modalities,
        unit_kinds=scope.unit_kinds,
        facets_any=_deduplicate((*scope.facets_any, *(item.core() for item in filters.any))),
        facets_all=_deduplicate((*scope.facets_all, *(item.core() for item in filters.all))),
        facets_none=_deduplicate((*scope.facets_none, *(item.core() for item in filters.none))),
    )


def metadata_filters_from_cli(
    policy: MetadataProjectionPolicy,
    *,
    tags: tuple[str, ...] = (),
    all_values: tuple[str, ...] = (),
    any_values: tuple[str, ...] = (),
    none_values: tuple[str, ...] = (),
) -> MetadataFilters:
    """Parse exact PATH=JSON_SCALAR values using the ingest projection codec."""

    if not isinstance(policy, MetadataProjectionPolicy):
        raise TypeError("policy must be a MetadataProjectionPolicy")
    namespaces = {
        projection.path: projection.namespace
        for projection in policy.projections
        if projection.mode in {"facet", "facet_many"}
    }

    def parse(values: tuple[str, ...]) -> tuple[MetadataFilter, ...]:
        parsed = []
        for item in values:
            path, separator, raw_value = item.partition("=")
            namespace = namespaces.get(path)
            if not separator or not path or namespace is None:
                raise ValueError("metadata filter path must name a projected facet")
            parsed.append(MetadataFilter(namespace, FACET_SCALAR_CODEC.parse_display(raw_value)))
        return tuple(parsed)

    return MetadataFilters(
        any=parse(any_values),
        all=(
            *(MetadataFilter("tag", value) for value in tags),
            *parse(all_values),
        ),
        none=parse(none_values),
    )


def _deduplicate(facets: tuple[Facet, ...]) -> tuple[Facet, ...]:
    return tuple(dict.fromkeys(facets))


__all__ = [
    "MetadataFilter",
    "MetadataFilters",
    "compile_metadata_filters",
    "metadata_filters_from_cli",
]
