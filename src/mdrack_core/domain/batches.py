"""Prepared resource graph values accepted by provider-free indexing."""

from __future__ import annotations

from dataclasses import dataclass

from .resources import RepresentationRecord, ResourceFacet, ResourceRecord, SearchUnitRecord
from .vectors import EmbeddingSpaceRecord, VectorRecord, _freeze_sequence


def _freeze_typed_sequence(
    value: object,
    field_name: str,
    item_type: type[object],
) -> tuple[object, ...]:
    items = _freeze_sequence(value, field_name, item_type.__name__)
    if any(not isinstance(item, item_type) for item in items):
        raise ValueError(f"{field_name} must contain only {item_type.__name__} values")
    return items


@dataclass(frozen=True)
class PreparedResourceBatch:
    """One immutable, fully prepared resource graph.

    Relationship, uniqueness, and vector-dimension validation belongs to the core
    indexing service. This value only freezes the typed graph supplied by a producer.
    """

    resource: ResourceRecord
    representations: tuple[RepresentationRecord, ...] = ()
    units: tuple[SearchUnitRecord, ...] = ()
    spaces: tuple[EmbeddingSpaceRecord, ...] = ()
    vectors: tuple[VectorRecord, ...] = ()
    facets: tuple[ResourceFacet, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.resource, ResourceRecord):
            raise ValueError("resource must be a ResourceRecord")
        fields: tuple[tuple[str, type[object]], ...] = (
            ("representations", RepresentationRecord),
            ("units", SearchUnitRecord),
            ("spaces", EmbeddingSpaceRecord),
            ("vectors", VectorRecord),
            ("facets", ResourceFacet),
        )
        for field_name, item_type in fields:
            object.__setattr__(
                self,
                field_name,
                _freeze_typed_sequence(getattr(self, field_name), field_name, item_type),
            )
