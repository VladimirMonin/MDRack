"""LM Studio embedding provider.

This module provides an embedding provider that calls the LM Studio
HTTP API (OpenAI-compatible /v1/embeddings endpoint). It uses httpx
for async HTTP requests.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from mdrack.embeddings.protocol import EmbeddingError, EmbeddingHealth

logger = logging.getLogger(__name__)


class LMStudioProvider:
    """Embedding provider that calls LM Studio HTTP API.

    Implements the EmbeddingProvider protocol using the OpenAI-compatible
    /v1/embeddings endpoint exposed by LM Studio.
    """

    def __init__(
        self,
        endpoint: str,
        model: str,
        dimensions: int,
        timeout: int = 30,
    ) -> None:
        """Initialize the LM Studio provider.

        Args:
            endpoint: Base URL of the LM Studio server (e.g. "http://localhost:1234").
            model: Model name to use for embeddings.
            dimensions: Expected embedding dimension size.
            timeout: HTTP request timeout in seconds (default: 30).
        """
        self._endpoint = endpoint.rstrip("/")
        self._model = model
        self._dimensions = dimensions
        self._timeout = timeout
        self._client = httpx.AsyncClient(timeout=timeout)

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    async def embed(
        self, texts: list[str], profile: str = "default"
    ) -> list[list[float]]:
        """Embed a batch of texts via LM Studio API.

        Args:
            texts: List of text strings to embed.
            profile: Embedding profile name (ignored for LM Studio).

        Returns:
            List of embedding vectors, one per input text.

        Raises:
            EmbeddingError: On timeout, connection error, HTTP error,
                or dimension mismatch.
        """
        url = f"{self._endpoint}/v1/embeddings"
        payload: dict[str, Any] = {
            "model": self._model,
            "input": texts,
        }

        try:
            response = await self._client.post(url, json=payload)
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            logger.error("Timeout calling LM Studio: %s", exc)
            raise EmbeddingError(f"Timeout calling LM Studio: {exc}") from exc
        except httpx.HTTPStatusError as exc:
            logger.error("HTTP error from LM Studio: %s", exc)
            raise EmbeddingError(f"HTTP error from LM Studio: {exc}") from exc
        except httpx.RequestError as exc:
            logger.error("Request error calling LM Studio: %s", exc)
            raise EmbeddingError(f"Request error calling LM Studio: {exc}") from exc

        try:
            data = response.json()
        except Exception as exc:
            logger.error("Failed to parse LM Studio response: %s", exc)
            raise EmbeddingError(f"Failed to parse LM Studio response: {exc}") from exc

        if "data" not in data:
            raise EmbeddingError("Invalid response: missing 'data' field")

        try:
            embeddings = [item["embedding"] for item in data["data"]]
        except (KeyError, TypeError) as exc:
            raise EmbeddingError(f"Invalid embedding structure: {exc}") from exc

        # Validate dimensions
        for i, emb in enumerate(embeddings):
            if len(emb) != self._dimensions:
                raise EmbeddingError(
                    f"Dimension mismatch: expected {self._dimensions}, "
                    f"got {len(emb)} for text index {i}"
                )

        return embeddings

    async def embed_query(self, text: str, profile: str = "default") -> list[float]:
        """Embed a single query text with retrieval prefix.

        Args:
            text: Query text to embed.
            profile: Embedding profile name (ignored for LM Studio).

        Returns:
            Embedding vector for the query.
        """
        prefixed = f"Represent this document for retrieval: {text}"
        vectors = await self.embed([prefixed], profile)
        return vectors[0]

    async def health(self) -> EmbeddingHealth:
        """Check LM Studio health by attempting a small embedding.

        Returns:
            EmbeddingHealth with provider status information.
        """
        try:
            await self.embed(["health check"])
            return EmbeddingHealth(
                ok=True,
                provider="lmstudio",
                model=self._model,
                dimensions=self._dimensions,
                error=None,
            )
        except Exception as exc:
            return EmbeddingHealth(
                ok=False,
                provider="lmstudio",
                model=self._model,
                dimensions=self._dimensions,
                error=str(exc),
            )

    @property
    def dimensions(self) -> int:
        """Return the configured embedding dimension size."""
        return self._dimensions
