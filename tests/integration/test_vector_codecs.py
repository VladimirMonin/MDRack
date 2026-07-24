"""Byte-level conformance for standalone SQLite vector codecs."""

from __future__ import annotations

import math
import struct

import pytest

from mdrack.application.vector_values import canonicalize_float32
from mdrack_sqlite.vector_codecs import (
    FLOAT32_CODEC_ID,
    FLOAT64_CODEC_ID,
    LEGACY_JSON_F64_CODEC_ID,
    Float32LECodec,
    Float64LECodec,
    JsonF64Codec,
    VectorCodecRegistry,
)


def test_float64_codec_is_little_endian_and_lossless_including_signed_zero() -> None:
    values = (1.5, -0.0)

    encoded = Float64LECodec().encode(values, dimensions=2)
    decoded = Float64LECodec().decode(encoded, dimensions=2)

    assert encoded == struct.pack("<2d", *values)
    assert decoded == values
    assert math.copysign(1.0, decoded[1]) == -1.0


def test_float32_codec_accepts_only_canonical_values_and_has_deterministic_bytes() -> None:
    raw = (1.0 + 2**-30, -0.0)
    canonical = canonicalize_float32(raw)
    codec = Float32LECodec()

    encoded = codec.encode(canonical, dimensions=2)

    assert encoded == struct.pack("<2f", *canonical)
    assert codec.decode(encoded, dimensions=2) == canonical
    assert math.copysign(1.0, codec.decode(encoded, dimensions=2)[1]) == -1.0
    with pytest.raises(ValueError, match="canonical float32"):
        codec.encode(raw, dimensions=2)


@pytest.mark.parametrize("payload", [b"", b"\x00\x00\x00", struct.pack("<3f", 1.0, 2.0, 3.0)])
def test_float32_codec_rejects_invalid_payload_lengths(payload: bytes) -> None:
    with pytest.raises(ValueError, match="payload length"):
        Float32LECodec().decode(payload, dimensions=2)


def test_legacy_json_codec_is_diagnostic_read_only() -> None:
    codec = JsonF64Codec()

    decoded = codec.decode(b"[1.5,-0.0]", dimensions=2)

    assert decoded == (1.5, -0.0)
    assert math.copysign(1.0, decoded[1]) == -1.0
    with pytest.raises(ValueError, match="read-only"):
        codec.encode(decoded, dimensions=2)


def test_codec_registry_resolves_only_known_ids() -> None:
    registry = VectorCodecRegistry.default()

    assert registry.get(FLOAT64_CODEC_ID).codec_id == FLOAT64_CODEC_ID
    assert registry.get(FLOAT32_CODEC_ID).codec_id == FLOAT32_CODEC_ID
    assert registry.get(LEGACY_JSON_F64_CODEC_ID).codec_id == LEGACY_JSON_F64_CODEC_ID
    with pytest.raises(ValueError, match="unknown vector codec"):
        registry.get("unknown-codec")
