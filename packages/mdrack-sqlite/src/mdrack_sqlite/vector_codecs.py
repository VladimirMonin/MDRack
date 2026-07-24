"""Standard-library binary codecs for canonical SQLite vector payloads."""

from __future__ import annotations

import json
import math
import struct
from collections.abc import Mapping, Sequence
from typing import Protocol, runtime_checkable

FLOAT64_CODEC_ID = "ieee754-f64-le-v1"
FLOAT32_CODEC_ID = "ieee754-f32-le-v1"
LEGACY_JSON_F64_CODEC_ID = "json-f64-v1"


@runtime_checkable
class VectorCodec(Protocol):
    """Codec boundary for payload bytes; value policy remains outside the core."""

    codec_id: str
    write_enabled: bool

    def encode(self, vector: Sequence[object], *, dimensions: int) -> bytes: ...

    def decode(self, payload: object, *, dimensions: int) -> tuple[float, ...]: ...


def _finite_vector(vector: Sequence[object], dimensions: int, *, field_name: str) -> tuple[float, ...]:
    if not isinstance(vector, Sequence) or isinstance(vector, (str, bytes, bytearray)) or not vector:
        raise ValueError(f"{field_name} must be a non-empty ordered sequence")
    if type(dimensions) is not int or dimensions < 1:
        raise ValueError("dimensions must be a positive integer")
    values: list[float] = []
    for value in vector:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{field_name} must contain finite numbers")
        try:
            number = float(value)
        except (OverflowError, ValueError):
            raise ValueError(f"{field_name} must contain finite numbers") from None
        if not math.isfinite(number):
            raise ValueError(f"{field_name} must contain finite numbers")
        values.append(number)
    if len(values) != dimensions:
        raise ValueError("vector dimension mismatch")
    return tuple(values)


def _payload(payload: object, *, dimensions: int, width: int) -> bytes:
    if not isinstance(payload, bytes):
        raise ValueError("embedding must be bytes")
    if type(dimensions) is not int or dimensions < 1:
        raise ValueError("dimensions must be a positive integer")
    if len(payload) != dimensions * width:
        raise ValueError("vector payload length does not match dimensions")
    return payload


def _same_ieee_value(left: float, right: float) -> bool:
    if left != right:
        return False
    return left != 0.0 or math.copysign(1.0, left) == math.copysign(1.0, right)


def codec_id_from_metadata(metadata: Mapping[str, object]) -> str:
    """Resolve the only writable codec compatible with canonical space metadata."""
    policy = metadata.get("vector_value_policy")
    codec = metadata.get("vector_codec")
    if policy is None:
        if codec is None or codec == FLOAT64_CODEC_ID:
            return FLOAT64_CODEC_ID
        if codec == FLOAT32_CODEC_ID:
            raise ValueError("float32 codec requires an explicit float32 value policy")
        if codec == LEGACY_JSON_F64_CODEC_ID:
            raise ValueError("legacy JSON codec is diagnostic read-only")
        raise ValueError("unknown vector codec")
    if policy != "ieee754-f32-canonical-v1" or codec != FLOAT32_CODEC_ID:
        raise ValueError("vector value policy and codec are incompatible")
    return FLOAT32_CODEC_ID


class Float64LECodec:
    """Lossless little-endian binary IEEE-754 float64 payload codec."""

    codec_id = FLOAT64_CODEC_ID
    write_enabled = True

    def encode(self, vector: Sequence[object], *, dimensions: int) -> bytes:
        values = _finite_vector(vector, dimensions, field_name="vector")
        try:
            return struct.pack(f"<{dimensions}d", *values)
        except struct.error:
            raise ValueError("vector cannot be encoded as float64") from None

    def decode(self, payload: object, *, dimensions: int) -> tuple[float, ...]:
        encoded = _payload(payload, dimensions=dimensions, width=8)
        try:
            values = struct.unpack(f"<{dimensions}d", encoded)
        except struct.error:
            raise ValueError("vector payload is corrupt") from None
        return _finite_vector(values, dimensions, field_name="embedding")


class Float32LECodec:
    """Canonical little-endian IEEE-754 float32 codec for explicitly marked spaces."""

    codec_id = FLOAT32_CODEC_ID
    write_enabled = True

    def encode(self, vector: Sequence[object], *, dimensions: int) -> bytes:
        values = _finite_vector(vector, dimensions, field_name="vector")
        try:
            encoded = struct.pack(f"<{dimensions}f", *values)
            canonical = struct.unpack(f"<{dimensions}f", encoded)
        except (OverflowError, struct.error):
            raise ValueError("vector value is outside the float32 range") from None
        if any(not _same_ieee_value(value, rounded) for value, rounded in zip(values, canonical, strict=True)):
            raise ValueError("vector must already use canonical float32 values")
        return encoded

    def decode(self, payload: object, *, dimensions: int) -> tuple[float, ...]:
        encoded = _payload(payload, dimensions=dimensions, width=4)
        try:
            values = struct.unpack(f"<{dimensions}f", encoded)
        except struct.error:
            raise ValueError("vector payload is corrupt") from None
        return _finite_vector(values, dimensions, field_name="embedding")


class JsonF64Codec:
    """Read-only diagnostic decoder for canonical legacy JSON float64 payloads."""

    codec_id = LEGACY_JSON_F64_CODEC_ID
    write_enabled = False

    def encode(self, vector: Sequence[object], *, dimensions: int) -> bytes:
        del vector, dimensions
        raise ValueError("legacy JSON vector codec is read-only")

    def decode(self, payload: object, *, dimensions: int) -> tuple[float, ...]:
        if not isinstance(payload, bytes):
            raise ValueError("embedding must be bytes")
        try:
            decoded = json.loads(payload.decode("utf-8", "strict"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("legacy JSON vector payload is invalid") from None
        if not isinstance(decoded, list):
            raise ValueError("legacy JSON vector payload must be an array")
        values = _finite_vector(decoded, dimensions, field_name="embedding")
        canonical = json.dumps(values, allow_nan=False, separators=(",", ":")).encode("utf-8")
        if canonical != payload:
            raise ValueError("legacy JSON vector payload is not canonical")
        return values


class VectorCodecRegistry:
    """Closed codec registry: unknown identifiers fail before SQLite reads or writes."""

    def __init__(self, codecs: Sequence[VectorCodec]) -> None:
        entries: dict[str, VectorCodec] = {}
        for codec in codecs:
            if not isinstance(codec, VectorCodec):
                raise TypeError("registry entries must implement VectorCodec")
            if codec.codec_id in entries:
                raise ValueError("vector codec ids must be unique")
            entries[codec.codec_id] = codec
        if not entries:
            raise ValueError("vector codec registry must not be empty")
        self._entries = entries

    @classmethod
    def default(cls) -> VectorCodecRegistry:
        return cls((Float64LECodec(), Float32LECodec(), JsonF64Codec()))

    def get(self, codec_id: str) -> VectorCodec:
        if not isinstance(codec_id, str) or not codec_id:
            raise ValueError("vector codec id must be non-empty")
        try:
            return self._entries[codec_id]
        except KeyError:
            raise ValueError("unknown vector codec") from None

    @property
    def codecs(self) -> Mapping[str, VectorCodec]:
        return dict(self._entries)


def decode_vector_payload(
    payload: object,
    *,
    dimensions: int,
    metadata: Mapping[str, object],
    registry: VectorCodecRegistry | None = None,
) -> tuple[float, ...]:
    """Decode current binary bytes and canonical unmarked legacy JSON read-only data."""
    active_registry = registry or VectorCodecRegistry.default()
    codec_id = codec_id_from_metadata(metadata)
    try:
        return active_registry.get(codec_id).decode(payload, dimensions=dimensions)
    except ValueError:
        if codec_id != FLOAT64_CODEC_ID or metadata.get("vector_codec") is not None:
            raise
        return active_registry.get(LEGACY_JSON_F64_CODEC_ID).decode(payload, dimensions=dimensions)
