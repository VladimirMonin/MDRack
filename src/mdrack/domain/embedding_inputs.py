"""Immutable prepared provider inputs; no source lookup or model I/O."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from mdrack_core import EmbeddingSpaceRecord, VectorRecord


class EncodingRole(StrEnum):
    DOCUMENT = "document"
    QUERY = "query"
    SIMILARITY = "similarity"


@dataclass(frozen=True)
class TextInput:
    text: str = field(repr=False)
    identity: str

    def __post_init__(self) -> None:
        if (not isinstance(self.text, str) or not self.text.strip()
                or not isinstance(self.identity, str) or not self.identity):
            raise ValueError("invalid_prepared_input")

    @property
    def modality(self) -> str:
        return "text"

    @property
    def byte_size(self) -> int:
        return len(self.text.encode("utf-8"))


@dataclass(frozen=True)
class ImageInput:
    content: bytes = field(repr=False)
    media_type: str
    identity: str

    def __post_init__(self) -> None:
        if (type(self.content) is not bytes or not self.content
                or not isinstance(self.identity, str) or not self.identity):
            raise ValueError("invalid_prepared_input")
        if self.media_type not in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
            raise ValueError("unsupported_media_type")

    @property
    def modality(self) -> str:
        return "image"

    @property
    def byte_size(self) -> int:
        return len(self.content)


PreparedInput = TextInput | ImageInput


@dataclass(frozen=True)
class InputCapability:
    role: EncodingRole
    input_modality: str
    candidate_modalities: tuple[str, ...]
    max_input_bytes: int

    def __post_init__(self) -> None:
        if not isinstance(self.role, EncodingRole) or self.input_modality not in {"text", "image"}:
            raise ValueError("invalid_capability")
        if (type(self.candidate_modalities) is not tuple or not self.candidate_modalities
                or set(self.candidate_modalities) - {"text", "image"}):
            raise ValueError("invalid_capability")
        if type(self.max_input_bytes) is not int or self.max_input_bytes < 1:
            raise ValueError("invalid_capability")


@dataclass(frozen=True)
class RouteCapabilities:
    inputs: tuple[InputCapability, ...]
    reuse_document_vector: bool = False

    def __post_init__(self) -> None:
        if type(self.inputs) is not tuple or any(not isinstance(i, InputCapability) for i in self.inputs):
            raise ValueError("invalid_capability")
        keys = [(i.role, i.input_modality) for i in self.inputs]
        if len(set(keys)) != len(keys) or type(self.reuse_document_vector) is not bool:
            raise ValueError("invalid_capability")

    def require(self, role: EncodingRole, modality: str, candidates: tuple[str, ...] = ()) -> InputCapability:
        if not isinstance(role, EncodingRole):
            raise ValueError("invalid_encoding_role")
        for item in self.inputs:
            if item.role == role and item.input_modality == modality:
                if set(candidates) <= set(item.candidate_modalities):
                    return item
        raise ValueError("unsupported_embedding_route")

    def validate(self, value: PreparedInput, role: EncodingRole, candidates: tuple[str, ...] = ()) -> None:
        capability = self.require(role, value.modality, candidates)
        if value.byte_size > capability.max_input_bytes:
            raise ValueError("embedding_input_too_large")


@dataclass(frozen=True)
class EmbeddingResult:
    vector: tuple[float, ...] = field(repr=False)
    space: EmbeddingSpaceRecord

    def __post_init__(self) -> None:
        record = VectorRecord("prepared", self.space.space_id, self.vector)
        if len(record.vector) != self.space.dimensions:
            raise ValueError("embedding_dimension_mismatch")
        if self.space.metric == "cosine" and not any(record.vector):
            raise ValueError("invalid_cosine_vector")
        object.__setattr__(self, "vector", record.vector)
