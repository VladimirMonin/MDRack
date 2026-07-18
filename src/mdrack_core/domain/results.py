"""Immutable provider-neutral search result and degradation records."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from .common import (
    JSONValue,
    freeze_json_mapping,
    normalize_optional_request_id,
    require_finite_number,
    require_integer,
    require_non_empty,
    require_optional_non_empty,
)
from .errors import DegradationCategory
from .search import TARGETS, RankedCandidate, SearchScope
from .vectors import _freeze_sequence


def _empty_mapping() -> dict[str, JSONValue]:
    return {}


def _freeze_typed(value: object, field_name: str, item_type: type[object]) -> tuple[object, ...]:
    items = _freeze_sequence(value, field_name, item_type.__name__)
    if any(not isinstance(item, item_type) for item in items):
        raise ValueError(f"{field_name} must contain only {item_type.__name__} values")
    return items


@dataclass(frozen=True)
class Degradation:
    branch_id: str
    category: DegradationCategory

    def __post_init__(self) -> None:
        require_non_empty(self.branch_id, "branch_id")
        if not isinstance(self.category, DegradationCategory):
            raise ValueError("category must be a DegradationCategory")


@dataclass(frozen=True)
class SearchResultItem:
    logical_id: str
    resource_id: str
    unit_id: str | None
    score: float
    rank: int
    evidence: tuple[RankedCandidate, ...] = ()
    metadata: Mapping[str, JSONValue] = field(default_factory=_empty_mapping)

    def __post_init__(self) -> None:
        require_non_empty(self.logical_id, "logical_id")
        require_non_empty(self.resource_id, "resource_id")
        require_optional_non_empty(self.unit_id, "unit_id")
        object.__setattr__(self, "score", require_finite_number(self.score, "score"))
        require_integer(self.rank, "rank", minimum=1)
        object.__setattr__(
            self,
            "evidence",
            _freeze_typed(self.evidence, "evidence", RankedCandidate),
        )
        object.__setattr__(self, "metadata", freeze_json_mapping(self.metadata, "metadata"))


@dataclass(frozen=True)
class SearchResult:
    target: str
    items: tuple[SearchResultItem, ...]
    degradations: tuple[Degradation, ...] = ()
    request_id: str | None = None

    def __post_init__(self) -> None:
        require_non_empty(self.target, "target")
        if self.target not in TARGETS:
            raise ValueError("target must be unit or resource")
        object.__setattr__(self, "request_id", normalize_optional_request_id(self.request_id))
        object.__setattr__(self, "items", _freeze_typed(self.items, "items", SearchResultItem))
        object.__setattr__(
            self,
            "degradations",
            _freeze_typed(self.degradations, "degradations", Degradation),
        )


@dataclass(frozen=True)
class SimilarityRequest:
    query_unit_id: str
    space_id: str
    scope: SearchScope
    limit: int
    exclude_same_resource: bool = True

    def __post_init__(self) -> None:
        require_non_empty(self.query_unit_id, "query_unit_id")
        require_non_empty(self.space_id, "space_id")
        if not isinstance(self.scope, SearchScope):
            raise ValueError("scope must be a SearchScope")
        require_integer(self.limit, "limit", minimum=1)
        if not isinstance(self.exclude_same_resource, bool):
            raise ValueError("exclude_same_resource must be a boolean")


@dataclass(frozen=True)
class SimilarityResult:
    query_unit_id: str
    space_id: str
    items: tuple[SearchResultItem, ...]
    degradations: tuple[Degradation, ...] = ()

    def __post_init__(self) -> None:
        require_non_empty(self.query_unit_id, "query_unit_id")
        require_non_empty(self.space_id, "space_id")
        object.__setattr__(self, "items", _freeze_typed(self.items, "items", SearchResultItem))
        object.__setattr__(
            self,
            "degradations",
            _freeze_typed(self.degradations, "degradations", Degradation),
        )
