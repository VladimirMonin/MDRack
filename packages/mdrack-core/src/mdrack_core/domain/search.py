"""Provider-free search request and candidate records."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from .common import (
    JSONValue,
    freeze_json_mapping,
    normalize_optional_request_id,
    require_finite_number,
    require_integer,
    require_non_empty,
    require_optional_non_empty,
)
from .resources import Facet, Locator
from .vectors import _freeze_sequence, freeze_vector

TARGET_UNIT = "unit"
TARGET_RESOURCE = "resource"
TARGETS = frozenset({TARGET_UNIT, TARGET_RESOURCE})


class ScoreKind(StrEnum):
    """Semantic origin of a candidate or result score."""

    ADAPTER_RAW = "adapter_raw"
    RRF = "rrf"


class RankKind(StrEnum):
    """Population in which a rank is dense and one-based."""

    ADAPTER_CANDIDATE = "adapter_candidate"
    RESULT = "result"


def _empty_mapping() -> dict[str, JSONValue]:
    return {}


def _freeze_strings(value: object, field_name: str) -> tuple[str, ...]:
    items = _freeze_sequence(value, field_name, "non-empty strings")
    for item in items:
        require_non_empty(item, field_name)
    return tuple(item for item in items if isinstance(item, str))


def _freeze_facets(value: object, field_name: str) -> tuple[Facet, ...]:
    items = _freeze_sequence(value, field_name, "Facet values")
    if any(not isinstance(item, Facet) for item in items):
        raise ValueError(f"{field_name} must contain only Facet values")
    return tuple(item for item in items if isinstance(item, Facet))


@dataclass(frozen=True)
class SearchScope:
    resource_kinds: tuple[str, ...] = ()
    media_types: tuple[str, ...] = ()
    source_namespaces: tuple[str, ...] = ()
    representation_kinds: tuple[str, ...] = ()
    modalities: tuple[str, ...] = ()
    unit_kinds: tuple[str, ...] = ()
    facets_any: tuple[Facet, ...] = ()
    facets_all: tuple[Facet, ...] = ()
    facets_none: tuple[Facet, ...] = ()

    def __post_init__(self) -> None:
        for field_name in (
            "resource_kinds",
            "media_types",
            "source_namespaces",
            "representation_kinds",
            "modalities",
            "unit_kinds",
        ):
            object.__setattr__(self, field_name, _freeze_strings(getattr(self, field_name), field_name))
        for field_name in ("facets_any", "facets_all", "facets_none"):
            object.__setattr__(self, field_name, _freeze_facets(getattr(self, field_name), field_name))


@dataclass(frozen=True)
class BranchScopeOverride:
    """Categorical-only branch narrowing; facet clauses remain request-global."""

    resource_kinds: tuple[str, ...] = ()
    media_types: tuple[str, ...] = ()
    source_namespaces: tuple[str, ...] = ()
    representation_kinds: tuple[str, ...] = ()
    modalities: tuple[str, ...] = ()
    unit_kinds: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field_name in (
            "resource_kinds",
            "media_types",
            "source_namespaces",
            "representation_kinds",
            "modalities",
            "unit_kinds",
        ):
            object.__setattr__(self, field_name, _freeze_strings(getattr(self, field_name), field_name))


@dataclass(frozen=True)
class LexicalBranch:
    branch_id: str
    query: str
    weight: float = 1.0
    candidate_limit: int = 100
    scope_override: BranchScopeOverride | None = None

    def __post_init__(self) -> None:
        require_non_empty(self.branch_id, "branch_id")
        require_non_empty(self.query, "query")
        weight = require_finite_number(self.weight, "weight")
        if weight <= 0:
            raise ValueError("weight must be positive")
        object.__setattr__(self, "weight", weight)
        require_integer(self.candidate_limit, "candidate_limit", minimum=1)
        if self.scope_override is not None and not isinstance(
            self.scope_override,
            BranchScopeOverride,
        ):
            raise ValueError("scope_override must be a BranchScopeOverride")


@dataclass(frozen=True)
class VectorBranch:
    branch_id: str
    space_id: str
    vector: tuple[float, ...]
    weight: float = 1.0
    candidate_limit: int = 100
    expected_fingerprint: str | None = None
    scope_override: BranchScopeOverride | None = None

    def __post_init__(self) -> None:
        require_non_empty(self.branch_id, "branch_id")
        require_non_empty(self.space_id, "space_id")
        object.__setattr__(self, "vector", freeze_vector(self.vector))
        weight = require_finite_number(self.weight, "weight")
        if weight <= 0:
            raise ValueError("weight must be positive")
        object.__setattr__(self, "weight", weight)
        require_integer(self.candidate_limit, "candidate_limit", minimum=1)
        require_optional_non_empty(self.expected_fingerprint, "expected_fingerprint")
        if self.scope_override is not None and not isinstance(
            self.scope_override,
            BranchScopeOverride,
        ):
            raise ValueError("scope_override must be a BranchScopeOverride")


@dataclass(frozen=True)
class SearchRequest:
    lexical_branches: tuple[LexicalBranch, ...]
    vector_branches: tuple[VectorBranch, ...]
    scope: SearchScope
    target: str
    limit: int
    rrf_k: int = 60
    evidence_limit_per_resource: int = 3
    allow_partial: bool = True
    request_id: str | None = None

    def __post_init__(self) -> None:
        lexical_items = _freeze_sequence(
            self.lexical_branches,
            "lexical_branches",
            "LexicalBranch values",
        )
        vector_items = _freeze_sequence(
            self.vector_branches,
            "vector_branches",
            "VectorBranch values",
        )
        if any(not isinstance(item, LexicalBranch) for item in lexical_items):
            raise ValueError("lexical_branches must contain only LexicalBranch values")
        if any(not isinstance(item, VectorBranch) for item in vector_items):
            raise ValueError("vector_branches must contain only VectorBranch values")
        lexical = tuple(item for item in lexical_items if isinstance(item, LexicalBranch))
        vectors = tuple(item for item in vector_items if isinstance(item, VectorBranch))
        if not lexical and not vectors:
            raise ValueError("at least one search branch is required")
        branch_ids = [item.branch_id for item in lexical]
        branch_ids.extend(item.branch_id for item in vectors)
        if len(branch_ids) != len(set(branch_ids)):
            raise ValueError("branch_id values must be unique")
        if not isinstance(self.scope, SearchScope):
            raise ValueError("scope must be a SearchScope")
        require_non_empty(self.target, "target")
        if self.target not in TARGETS:
            raise ValueError("target must be unit or resource")
        require_integer(self.limit, "limit", minimum=1)
        require_integer(self.rrf_k, "rrf_k", minimum=1)
        require_integer(
            self.evidence_limit_per_resource,
            "evidence_limit_per_resource",
            minimum=1,
        )
        if not isinstance(self.allow_partial, bool):
            raise ValueError("allow_partial must be a boolean")
        object.__setattr__(self, "request_id", normalize_optional_request_id(self.request_id))
        object.__setattr__(self, "lexical_branches", lexical)
        object.__setattr__(self, "vector_branches", vectors)


@dataclass(frozen=True)
class RankedCandidate:
    unit_id: str
    resource_id: str
    representation_id: str
    rank: int
    raw_score: float
    branch_id: str
    evidence_locator: Locator
    metadata: Mapping[str, JSONValue] = field(default_factory=_empty_mapping)
    score_kind: ScoreKind = ScoreKind.ADAPTER_RAW
    rank_kind: RankKind = RankKind.ADAPTER_CANDIDATE

    def __post_init__(self) -> None:
        require_non_empty(self.unit_id, "unit_id")
        require_non_empty(self.resource_id, "resource_id")
        require_non_empty(self.representation_id, "representation_id")
        require_integer(self.rank, "rank", minimum=1)
        object.__setattr__(self, "raw_score", require_finite_number(self.raw_score, "raw_score"))
        require_non_empty(self.branch_id, "branch_id")
        if not isinstance(self.evidence_locator, Locator):
            raise ValueError("evidence_locator must be a Locator")
        object.__setattr__(self, "metadata", freeze_json_mapping(self.metadata, "metadata"))
        if self.score_kind is not ScoreKind.ADAPTER_RAW:
            raise ValueError("RankedCandidate score_kind must be adapter_raw")
        if self.rank_kind is not RankKind.ADAPTER_CANDIDATE:
            raise ValueError("RankedCandidate rank_kind must be adapter_candidate")
