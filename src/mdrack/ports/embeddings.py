"""Embedding provider port."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class EmbeddingHealth:
    ok: bool
    provider: str
    model: str
    dimensions: int
    error: str | None = None
    requested_dimensions: int | None = None
    returned_dimensions: int | None = None
    vector_length_valid: bool | None = None
    mrl_status: str = "unsupported_by_runtime"


class EmbeddingError(Exception):
    """A privacy-safe embedding provider failure."""


@runtime_checkable
class EmbeddingProvider(Protocol):
    async def embed(self, texts: list[str], profile: str = "default") -> list[list[float]]: ...

    async def embed_query(self, text: str, profile: str = "default") -> list[float]: ...

    async def health(self) -> EmbeddingHealth: ...

    @property
    def dimensions(self) -> int: ...
