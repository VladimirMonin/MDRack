"""Conformance tests for the app-owned float32 vector value policy."""

from __future__ import annotations

import math
import random
import struct

import pytest

from mdrack.application.vector_values import (
    FLOAT32_VALUE_POLICY,
    canonicalize_float32,
    value_policy_metadata,
)
from mdrack.config.models import MDRackConfig


def test_float32_value_policy_is_idempotent_deterministic_and_preserves_signed_zero() -> None:
    raw = (1.0 + 2**-30, -0.0, float.fromhex("0x1.fffffep+127"))

    first = canonicalize_float32(raw)
    second = canonicalize_float32(first)

    assert first == second
    assert struct.pack("<3f", *first) == struct.pack("<3f", *second)
    assert math.copysign(1.0, first[1]) == -1.0
    assert first[0] != raw[0]


def test_float32_value_policy_is_conformant_for_deterministic_finite_bit_patterns() -> None:
    random_bits = random.Random(20260724)
    values = []
    for _ in range(512):
        value = struct.unpack("<f", random_bits.randbytes(4))[0]
        if math.isfinite(value):
            values.append(value)

    canonical = canonicalize_float32(values)

    assert canonicalize_float32(canonical) == canonical
    assert struct.pack(f"<{len(canonical)}f", *canonical) == struct.pack(
        f"<{len(canonical)}f", *canonicalize_float32(values)
    )


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), -float("inf"), float.fromhex("0x1.0p+128")])
def test_float32_value_policy_rejects_invalid_or_overflowing_values(value: object) -> None:
    with pytest.raises(ValueError):
        canonicalize_float32((value,))


def test_value_policy_metadata_is_explicit_and_rejects_unknown_policy() -> None:
    assert value_policy_metadata(None) == {}
    assert value_policy_metadata(FLOAT32_VALUE_POLICY) == {
        "vector_codec": "ieee754-f32-le-v1",
        "vector_value_policy": FLOAT32_VALUE_POLICY,
    }
    with pytest.raises(ValueError, match="unsupported vector value policy"):
        value_policy_metadata("float16")


def test_default_config_selects_the_explicit_float32_value_policy() -> None:
    assert MDRackConfig().embedding.vector_value_policy == FLOAT32_VALUE_POLICY
