"""Offline contracts for stable embedding profiles and runtime capabilities."""

from __future__ import annotations

from dataclasses import replace

from mdrack.domain.profiles import (
    EmbeddingCapabilities,
    EmbeddingProfile,
    validate_output_dimensions,
)


def _profile(**overrides: object) -> EmbeddingProfile:
    values = {
        "name": "balanced",
        "provider": "lmstudio",
        "runtime": "lmstudio-gui",
        "model_key": "qwen3-embedding-8b-q4",
        "model_family": "qwen3-embedding",
        "quantization": "q4_k_m",
        "output_dimensions": 1024,
        "query_instruction": "Represent the query for retrieval",
        "normalization_mode": "l2",
        "endpoint_family": "openai_embeddings",
    }
    values.update(overrides)
    return EmbeddingProfile(**values)


def test_profile_fingerprint_is_stable_and_covers_every_identity_field() -> None:
    profile = _profile()

    assert profile.fingerprint == _profile().fingerprint
    for field_name in (
        "provider",
        "runtime",
        "model_key",
        "model_family",
        "quantization",
        "output_dimensions",
        "query_instruction",
        "normalization_mode",
        "endpoint_family",
    ):
        current = getattr(profile, field_name)
        replacement = current + "-other" if isinstance(current, str) else current + 1
        assert replace(profile, **{field_name: replacement}).fingerprint != profile.fingerprint


def test_reduced_dimensions_require_runtime_support_without_claiming_live_support() -> None:
    profile = _profile(output_dimensions=1024)

    unknown = validate_output_dimensions(
        profile,
        EmbeddingCapabilities(max_output_dimensions=4096, supports_output_dimensions=None),
    )
    unsupported = validate_output_dimensions(
        profile,
        EmbeddingCapabilities(max_output_dimensions=4096, supports_output_dimensions=False),
    )
    supported = validate_output_dimensions(
        profile,
        EmbeddingCapabilities(max_output_dimensions=4096, supports_output_dimensions=True),
    )

    assert unknown.valid is False
    assert unknown.runtime_support is None
    assert unknown.reason == "runtime_capability_unknown"
    assert unsupported.valid is False
    assert unsupported.runtime_support is False
    assert unsupported.reason == "unsupported_by_runtime"
    assert supported.valid is True
    assert supported.runtime_support is True
    assert supported.reason is None


def test_dimensions_above_model_maximum_are_invalid_offline() -> None:
    result = validate_output_dimensions(
        _profile(output_dimensions=4097),
        EmbeddingCapabilities(max_output_dimensions=4096, supports_output_dimensions=True),
    )

    assert result.valid is False
    assert result.reason == "dimensions_exceed_model_maximum"
