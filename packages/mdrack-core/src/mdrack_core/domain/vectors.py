"""Embedding-space and ready-vector records."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from .common import (
    JSONValue,
    freeze_json_mapping,
    require_finite_number,
    require_integer,
    require_non_empty,
)

METRIC_COSINE = "cosine"
METRIC_DOT = "dot"
METRIC_L2 = "l2"
METRICS = frozenset({METRIC_COSINE, METRIC_DOT, METRIC_L2})


def _empty_mapping() -> dict[str, JSONValue]:
    return {}


def _freeze_sequence(value: object, field_name: str, item_description: str) -> tuple[object, ...]:
    """Freeze an explicitly ordered list or tuple, rejecting other iterables."""
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field_name} must be a sequence of {item_description}")
    return tuple(value)


def freeze_vector(value: object, field_name: str = "vector") -> tuple[float, ...]:
    """Validate and freeze a non-empty finite vector."""
    items = _freeze_sequence(value, field_name, "finite numbers")
    if not items:
        raise ValueError(f"{field_name} must not be empty")
    return tuple(
        require_finite_number(item, f"{field_name}[{index}]")
        for index, item in enumerate(items)
    )


@dataclass(frozen=True)
class EmbeddingSpaceRecord:
    space_id: str
    dimensions: int
    metric: str
    fingerprint: str
    metadata: Mapping[str, JSONValue] = field(default_factory=_empty_mapping)

    def __post_init__(self) -> None:
        require_non_empty(self.space_id, "space_id")
        require_integer(self.dimensions, "dimensions", minimum=1)
        require_non_empty(self.metric, "metric")
        if self.metric not in METRICS:
            raise ValueError("metric must be cosine, dot, or l2")
        require_non_empty(self.fingerprint, "fingerprint")
        object.__setattr__(self, "metadata", freeze_json_mapping(self.metadata, "metadata"))


@dataclass(frozen=True)
class VectorRecord:
    unit_id: str
    space_id: str
    vector: tuple[float, ...]

    def __post_init__(self) -> None:
        require_non_empty(self.unit_id, "unit_id")
        require_non_empty(self.space_id, "space_id")
        object.__setattr__(self, "vector", freeze_vector(self.vector))
