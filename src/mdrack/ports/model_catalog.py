"""Dynamic model catalog discovery port."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from mdrack.domain.profiles import CapabilityStatus, EmbeddingCapabilities

MRLStatus = Literal["tested", "unsupported_by_runtime"]


@dataclass(frozen=True)
class ModelDescriptor:
    key: str
    role: str
    state: str | None = None
    family: str | None = None
    quantization: str | None = None
    instance_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class EmbeddingCapabilityEvidence:
    """Evidence packet that cannot claim a test without dimension results."""

    model_id: str
    status: CapabilityStatus
    native_dimensions: int | None = None
    requested_dimensions: int | None = None
    returned_dimensions: int | None = None
    vector_length_valid: bool | None = None

    def __post_init__(self) -> None:
        if not self.model_id.strip():
            raise ValueError("model_id must not be empty")
        evidence = (
            self.native_dimensions,
            self.requested_dimensions,
            self.returned_dimensions,
            self.vector_length_valid,
        )
        if self.status == "tested" and any(value is None for value in evidence):
            raise ValueError("tested capability requires complete dimension evidence")
        if self.status != "tested" and any(value is not None for value in evidence):
            raise ValueError("untested capability must not contain runtime evidence")

    @property
    def mrl_status(self) -> MRLStatus:
        if (
            self.status == "tested"
            and self.vector_length_valid is True
            and self.native_dimensions is not None
            and self.requested_dimensions is not None
            and self.requested_dimensions < self.native_dimensions
            and self.requested_dimensions == self.returned_dimensions
        ):
            return "tested"
        return "unsupported_by_runtime"

    def as_dict(self) -> dict[str, object]:
        return {
            "model_id": self.model_id,
            "status": self.status,
            "native_dimensions": self.native_dimensions,
            "requested_dimensions": self.requested_dimensions,
            "returned_dimensions": self.returned_dimensions,
            "vector_length_valid": self.vector_length_valid,
            "mrl_status": self.mrl_status,
        }


@runtime_checkable
class ModelCatalogProvider(Protocol):
    async def list_models(self) -> list[ModelDescriptor]: ...

    async def embedding_capabilities(self, model_key: str) -> EmbeddingCapabilities: ...

    async def supports_reranking(self, model_key: str) -> bool | None: ...
