"""Stable embedding profile identity and offline capability validation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal


class IncompatibleEmbeddingProfileError(ValueError):
    """The active profile name is already bound to another fingerprint."""

    code = "incompatible_embedding_profile"

    def __init__(self, unsafe_detail: str | None = None) -> None:
        del unsafe_detail
        super().__init__(self.code)


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
    instruction_profile: str = "default"
    schema_version: int = 1
    vector_value_policy: str | None = None

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
            self.instruction_profile,
        )
        if any(not value.strip() for value in identity):
            raise ValueError("embedding profile identity fields must not be empty")
        if self.output_dimensions < 1:
            raise ValueError("output_dimensions must be positive")
        if self.schema_version < 1:
            raise ValueError("schema_version must be positive")
        if self.vector_value_policy not in {None, "ieee754-f32-canonical-v1"}:
            raise ValueError("vector_value_policy is unsupported")

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
            "instruction_profile": self.instruction_profile,
            "query_instruction_hash": self.query_instruction_hash,
            "runtime": self.runtime,
            "schema_version": self.schema_version,
            "vector_value_policy": self.vector_value_policy,
        }
        canonical = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @property
    def query_instruction_hash(self) -> str:
        return hashlib.sha256(self.query_instruction.encode("utf-8")).hexdigest()


CapabilityStatus = Literal["tested", "not_installed", "unsupported", "not_tested"]


@dataclass(frozen=True)
class EmbeddingCapabilities:
    """Observed or declared runtime limits; ``None`` means not probed."""

    status: CapabilityStatus = "not_tested"
    max_output_dimensions: int | None = None
    supports_output_dimensions: bool | None = None
    supported_output_dimensions: tuple[int, ...] = ()
    requested_dimensions: int | None = None
    returned_dimensions: int | None = None
    vector_length_valid: bool | None = None

    def __post_init__(self) -> None:
        if self.status == "tested" and (
            self.returned_dimensions is None or self.vector_length_valid is None
        ):
            raise ValueError("tested capability requires returned dimensions and vector validation")
        for value in (
            self.max_output_dimensions,
            self.requested_dimensions,
            self.returned_dimensions,
        ):
            if value is not None and value < 1:
                raise ValueError("embedding dimensions must be positive")


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
