"""Stable embedding profile identity and offline capability validation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


class IncompatibleEmbeddingProfileError(ValueError):
    """The active profile name is already bound to another fingerprint."""


@dataclass(frozen=True)
class EmbeddingProfile:
    """Complete identity of vectors that may coexist in one active profile."""

    name: str
    provider: str
    runtime: str
    model_key: str
    model_family: str
    quantization: str
    output_dimensions: int
    query_instruction: str
    normalization_mode: str
    endpoint_family: str

    def __post_init__(self) -> None:
        identity = (
            self.name,
            self.provider,
            self.runtime,
            self.model_key,
            self.model_family,
            self.quantization,
            self.query_instruction,
            self.normalization_mode,
            self.endpoint_family,
        )
        if any(not value.strip() for value in identity):
            raise ValueError("embedding profile identity fields must not be empty")
        if self.output_dimensions < 1:
            raise ValueError("output_dimensions must be positive")

    @property
    def fingerprint(self) -> str:
        payload = {
            "endpoint_family": self.endpoint_family,
            "model_family": self.model_family,
            "model_key": self.model_key,
            "normalization_mode": self.normalization_mode,
            "output_dimensions": self.output_dimensions,
            "provider": self.provider,
            "quantization": self.quantization,
            "query_instruction": self.query_instruction,
            "runtime": self.runtime,
        }
        canonical = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @property
    def query_instruction_hash(self) -> str:
        return hashlib.sha256(self.query_instruction.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class EmbeddingCapabilities:
    """Observed or declared runtime limits; ``None`` means not probed."""

    max_output_dimensions: int | None = None
    supports_output_dimensions: bool | None = None
    supported_output_dimensions: tuple[int, ...] = ()


@dataclass(frozen=True)
class DimensionValidation:
    valid: bool
    runtime_support: bool | None
    reason: str | None = None


def validate_output_dimensions(
    profile: EmbeddingProfile,
    capabilities: EmbeddingCapabilities,
) -> DimensionValidation:
    """Validate MRL/output dimensions without converting unknown support to a claim."""
    maximum = capabilities.max_output_dimensions
    requested = profile.output_dimensions
    if maximum is not None and requested > maximum:
        return DimensionValidation(False, capabilities.supports_output_dimensions, "dimensions_exceed_model_maximum")
    if capabilities.supported_output_dimensions and requested not in capabilities.supported_output_dimensions:
        return DimensionValidation(False, False, "unsupported_by_runtime")
    reduced = maximum is not None and requested < maximum
    if reduced and capabilities.supports_output_dimensions is None:
        return DimensionValidation(False, None, "runtime_capability_unknown")
    if reduced and capabilities.supports_output_dimensions is False:
        return DimensionValidation(False, False, "unsupported_by_runtime")
    return DimensionValidation(True, capabilities.supports_output_dimensions, None)
