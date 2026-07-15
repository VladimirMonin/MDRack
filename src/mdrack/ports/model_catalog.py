"""Dynamic model catalog discovery port."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from mdrack.domain.profiles import EmbeddingCapabilities


@dataclass(frozen=True)
class ModelDescriptor:
    key: str
    role: str
    state: str | None = None
    family: str | None = None
    quantization: str | None = None
    instance_ids: tuple[str, ...] = ()


@runtime_checkable
class ModelCatalogProvider(Protocol):
    async def list_models(self) -> list[ModelDescriptor]: ...

    async def embedding_capabilities(self, model_key: str) -> EmbeddingCapabilities: ...

    async def supports_reranking(self, model_key: str) -> bool | None: ...
