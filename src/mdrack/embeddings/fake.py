"""Fake embedding provider for tests.

This module provides a deterministic embedding provider that generates
hash-based fake vectors. It is intended ONLY for testing and should
never be used in production.
"""

from __future__ import annotations

import hashlib
from typing import Sequence

from mdrack.ports.embeddings import EmbeddingHealth


class FakeEmbeddingProvider:
    """Deterministic fake embedding provider for testing.

    Generates vectors using a hash-based approach so that the same text
    always produces the same vector. This allows tests to verify
    determinism without requiring a real LM Studio instance.

    The vectors are NOT semantically meaningful - they are only for
    verifying that the embedding pipeline works correctly.
    """

    def __init__(self, dimensions: int = 128, provider_name: str = "fake") -> None:
        """Initialize the fake embedding provider.

        Args:
            dimensions: Number of dimensions for output vectors (default: 128).
            provider_name: Name to report in health checks (default: "fake").
        """
        self._dimensions = dimensions
        self._provider_name = provider_name
        self._model_name = "fake-hash-v1"

    @property
    def dimensions(self) -> int:
        """Return the configured embedding dimension size."""
        return self._dimensions

    def _text_to_vector(self, text: str) -> list[float]:
        """Convert text to a deterministic vector using hash-based approach.

        This method uses SHA-256 hashing to generate deterministic
        pseudo-random values for each dimension.

        Args:
            text: Input text to convert to vector.

        Returns:
            List of float values representing the embedding vector.
        """
        # Use SHA-256 to create a deterministic seed from the text
        text_hash = hashlib.sha256(text.encode("utf-8")).digest()

        # Generate a list of deterministic float values
        vector: list[float] = []
        for i in range(self._dimensions):
            # Use the hash bytes to create deterministic values
            # Cycle through the 32-byte hash and add index variation
            byte_index = i % len(text_hash)
            byte_value = text_hash[byte_index]

            # Mix with index to ensure different dimensions have different values
            mixed = (byte_value * (i + 1)) % 256

            # Convert to float in range [-1.0, 1.0]
            value = (mixed / 127.5) - 1.0
            vector.append(value)

        # Normalize the vector to unit length for consistency
        norm = sum(v * v for v in vector) ** 0.5
        if norm > 0:
            vector = [v / norm for v in vector]

        return vector

    def _text_to_vector_sync(self, texts: Sequence[str]) -> list[list[float]]:
        """Synchronous version of embed for testing convenience.

        Args:
            texts: List of text strings to embed.

        Returns:
            List of embedding vectors, one per input text.
        """
        return [self._text_to_vector(text) for text in texts]

    def _health_sync(self) -> EmbeddingHealth:
        """Synchronous version of health for testing convenience.

        Returns:
            EmbeddingHealth with ok=True and provider information.
        """
        return EmbeddingHealth(
            ok=True,
            provider=self._provider_name,
            model=self._model_name,
            dimensions=self._dimensions,
            error=None,
        )

    async def embed(
        self, texts: Sequence[str], profile: str = "default"
    ) -> list[list[float]]:
        """Embed a batch of texts using deterministic hash-based vectors.

        Args:
            texts: List of text strings to embed.
            profile: Embedding profile name (ignored in fake provider).

        Returns:
            List of embedding vectors, one per input text.
        """
        return [self._text_to_vector(text) for text in texts]

    async def embed_query(self, text: str, profile: str = "default") -> list[float]:
        """Embed a single query text using deterministic hash-based vector.

        Args:
            text: Query text to embed.
            profile: Embedding profile name (ignored in fake provider).

        Returns:
            Embedding vector for the query.
        """
        return self._text_to_vector(text)

    async def health(self) -> EmbeddingHealth:
        """Return health status for the fake provider.

        Returns:
            EmbeddingHealth with ok=True and provider information.
        """
        return EmbeddingHealth(
            ok=True,
            provider=self._provider_name,
            model=self._model_name,
            dimensions=self._dimensions,
            error=None,
        )
