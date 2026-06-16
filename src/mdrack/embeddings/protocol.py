"""Embedding provider protocol and related types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class EmbeddingHealth:
    """Health status of an embedding provider."""

    ok: bool
    provider: str
    model: str
    dimensions: int
    error: str | None = None


class EmbeddingError(Exception):
    """Base exception for embedding provider errors."""


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Protocol for embedding providers.

    All embedding providers must implement this interface.
    The real provider calls LM Studio; the fake provider is for tests.
    """

    async def embed(
        self, texts: list[str], profile: str = "default"
    ) -> list[list[float]]:
        """Embed a batch of texts.

        Args:
            texts: List of text strings to embed.
            profile: Embedding profile name (default: "default").

        Returns:
            List of embedding vectors, one per input text.
        """
        ...

    async def embed_query(self, text: str, profile: str = "default") -> list[float]:
        """Embed a single query text.

        Args:
            text: Query text to embed.
            profile: Embedding profile name (default: "default").

        Returns:
            Embedding vector for the query.
        """
        ...

    async def health(self) -> EmbeddingHealth:
        """Check provider health status.

        Returns:
            EmbeddingHealth with provider status information.
        """
        ...

    @property
    def dimensions(self) -> int:
        """Return the embedding dimension size.

        Returns:
            Number of dimensions in the embedding vectors.
        """
        ...
