"""Deterministic provider-free whole-resource vector aggregation."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence


def weighted_centroid(
    vectors: Mapping[str, Sequence[float]],
    weights: Mapping[str, int],
    *,
    normalize: bool = True,
) -> tuple[float, ...]:
    """Return a token-weighted mean, optionally L2-normalized for cosine."""
    if not vectors or set(vectors) != set(weights):
        raise ValueError("vectors and weights must contain the same non-empty IDs")
    dimensions = {len(vector) for vector in vectors.values()}
    if len(dimensions) != 1 or not dimensions:
        raise ValueError("vectors must have one shared dimension")
    dimension = dimensions.pop()
    if dimension < 1:
        raise ValueError("vectors must not be empty")
    total_weight = 0
    result = [0.0] * dimension
    for identifier in sorted(vectors):
        weight = weights[identifier]
        if type(weight) is not int or weight < 1:
            raise ValueError("weights must be positive integers")
        vector = vectors[identifier]
        if any(
            not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value))
            for value in vector
        ):
            raise ValueError("vectors must contain finite numbers")
        total_weight += weight
        for index, value in enumerate(vector):
            result[index] += float(value) * weight
    result = [value / total_weight for value in result]
    if not normalize:
        return tuple(result)
    norm = math.sqrt(sum(value * value for value in result))
    if norm == 0.0:
        raise ValueError("whole-resource centroid has zero norm")
    return tuple(value / norm for value in result)


__all__ = ("weighted_centroid",)
