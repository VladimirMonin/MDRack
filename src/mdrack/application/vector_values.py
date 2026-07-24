"""App-owned numeric vector value policies and space metadata helpers."""

from __future__ import annotations

import math
import struct
from collections.abc import Mapping, Sequence
from dataclasses import replace

from mdrack_core.domain import EmbeddingSpaceRecord, PreparedResourceBatch, VectorRecord

FLOAT32_VALUE_POLICY = "ieee754-f32-canonical-v1"
FLOAT32_CODEC_ID = "ieee754-f32-le-v1"
VECTOR_CODEC_METADATA_KEY = "vector_codec"
VECTOR_VALUE_POLICY_METADATA_KEY = "vector_value_policy"


def validate_vector_value_policy(value: str | None) -> str | None:
    """Validate an app-owned storage-value policy without selecting a backend."""
    if value is None:
        return None
    if value != FLOAT32_VALUE_POLICY:
        raise ValueError("unsupported vector value policy")
    return value


def canonicalize_float32(vector: Sequence[object]) -> tuple[float, ...]:
    """Round finite values once to canonical IEEE-754 little-endian float32 values."""
    if not isinstance(vector, Sequence) or isinstance(vector, (str, bytes, bytearray)):
        raise ValueError("vector must be a non-empty ordered sequence")
    if not vector:
        raise ValueError("vector must be a non-empty ordered sequence")
    canonical: list[float] = []
    for value in vector:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("vector must contain finite numbers")
        try:
            number = float(value)
        except (OverflowError, ValueError):
            raise ValueError("vector must contain finite numbers") from None
        if not math.isfinite(number):
            raise ValueError("vector must contain finite numbers")
        try:
            rounded = struct.unpack("<f", struct.pack("<f", number))[0]
        except (OverflowError, struct.error):
            raise ValueError("vector value is outside the float32 range") from None
        if not math.isfinite(rounded):
            raise ValueError("vector value is outside the float32 range")
        canonical.append(rounded)
    return tuple(canonical)


def canonicalize_for_value_policy(vector: Sequence[object], value_policy: str | None) -> tuple[float, ...]:
    """Return finite values represented according to one explicit app policy."""
    policy = validate_vector_value_policy(value_policy)
    if policy == FLOAT32_VALUE_POLICY:
        return canonicalize_float32(vector)
    if not isinstance(vector, Sequence) or isinstance(vector, (str, bytes, bytearray)) or not vector:
        raise ValueError("vector must be a non-empty ordered sequence")
    values: list[float] = []
    for value in vector:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("vector must contain finite numbers")
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("vector must contain finite numbers")
        values.append(number)
    return tuple(values)


def value_policy_metadata(value_policy: str | None) -> dict[str, str]:
    """Return immutable-identity metadata required by the SQLite codec selection."""
    policy = validate_vector_value_policy(value_policy)
    if policy is None:
        return {}
    return {
        VECTOR_CODEC_METADATA_KEY: FLOAT32_CODEC_ID,
        VECTOR_VALUE_POLICY_METADATA_KEY: policy,
    }


def value_policy_from_space_metadata(metadata: Mapping[str, object]) -> str | None:
    """Read and validate one space's policy/codec pair before using its values."""
    if not isinstance(metadata, Mapping):
        raise ValueError("space metadata must be a mapping")
    policy = metadata.get(VECTOR_VALUE_POLICY_METADATA_KEY)
    codec = metadata.get(VECTOR_CODEC_METADATA_KEY)
    if policy is None:
        if codec is not None:
            raise ValueError("vector codec metadata requires a value policy")
        return None
    if not isinstance(policy, str):
        raise ValueError("vector value policy metadata must be a string")
    validate_vector_value_policy(policy)
    if codec != FLOAT32_CODEC_ID:
        raise ValueError("float32 value policy requires the float32 codec")
    return policy


def canonicalize_for_space(vector: Sequence[object], space: EmbeddingSpaceRecord) -> tuple[float, ...]:
    """Apply the persisted space policy to a provider or manifest vector."""
    if not isinstance(space, EmbeddingSpaceRecord):
        raise TypeError("space must be an EmbeddingSpaceRecord")
    return canonicalize_for_value_policy(vector, value_policy_from_space_metadata(space.metadata))


def apply_vector_value_policy(
    batch: PreparedResourceBatch,
    value_policy: str | None,
) -> PreparedResourceBatch:
    """Attach one policy to every vector space and canonicalize its values once."""
    policy = validate_vector_value_policy(value_policy)
    if policy is None or not batch.spaces:
        return batch
    spaces = tuple(
        replace(space, metadata={**dict(space.metadata), **value_policy_metadata(policy)})
        for space in batch.spaces
    )
    by_space = {space.space_id: space for space in spaces}
    vectors = tuple(
        VectorRecord(vector.unit_id, vector.space_id, canonicalize_for_space(vector.vector, by_space[vector.space_id]))
        for vector in batch.vectors
    )
    return replace(batch, spaces=spaces, vectors=vectors)


def canonicalize_prepared_batch_vectors(batch: PreparedResourceBatch) -> PreparedResourceBatch:
    """Canonicalize manifest or caller-owned vectors under their persisted space policies."""
    if not isinstance(batch, PreparedResourceBatch):
        raise TypeError("batch must be a PreparedResourceBatch")
    if not batch.vectors:
        return batch
    spaces = {space.space_id: space for space in batch.spaces}
    try:
        vectors = tuple(
            VectorRecord(
                vector.unit_id,
                vector.space_id,
                canonicalize_for_space(vector.vector, spaces[vector.space_id]),
            )
            for vector in batch.vectors
        )
    except KeyError:
        raise ValueError("every vector must reference an embedding space") from None
    return replace(batch, vectors=vectors)
