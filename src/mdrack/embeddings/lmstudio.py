"""LM Studio embedding provider.

This module provides an embedding provider that calls the LM Studio
HTTP API (OpenAI-compatible /v1/embeddings endpoint). It uses httpx
for async HTTP requests.
"""

from __future__ import annotations

import logging
from time import perf_counter
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from mdrack.embeddings.protocol import EmbeddingError, EmbeddingHealth

logger = logging.getLogger(__name__)


def _normalize_endpoint(endpoint: str) -> tuple[str, str]:
    """Return canonical LM Studio API base and embeddings URL."""
    raw = endpoint.rstrip("/")
    if raw.endswith("/v1/embeddings"):
        raw = raw[: -len("/v1/embeddings")]
    elif raw.endswith("/v1"):
        raw = raw[: -len("/v1")]

    parts = urlsplit(raw)
    base_path = parts.path.rstrip("/")
    api_path = f"{base_path}/v1" if base_path else "/v1"
    api_base = urlunsplit((parts.scheme, parts.netloc, api_path, "", ""))
    return api_base, f"{api_base}/embeddings"


def _endpoint_log_fields(api_endpoint: str) -> dict[str, str | int | None]:
    parts = urlsplit(api_endpoint)
    return {
        "endpoint_host": parts.hostname,
        "endpoint_port": parts.port,
        "endpoint_path": parts.path or "/",
    }


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
        self._provider_name = "lmstudio"
        self._model = model
        self._model_name = model
        self._endpoint, self._embeddings_url = _normalize_endpoint(endpoint)
        self._dimensions = dimensions
        self._timeout = timeout
        self._endpoint_fields = _endpoint_log_fields(self._endpoint)

    async def close(self) -> None:
        """Close provider resources.

        The provider creates short-lived clients per request, so there is
        nothing persistent to close here.
        """
        return None

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
        payload: dict[str, Any] = {
            "model": self._model,
            "input": texts,
        }
        started_at = perf_counter()
        logger.info(
            "llm.request.started provider=%s model=%s profile=%s input_count=%d "
            "endpoint_host=%s endpoint_port=%s endpoint_path=%s",
            self._provider_name,
            self._model,
            profile,
            len(texts),
            self._endpoint_fields["endpoint_host"],
            self._endpoint_fields["endpoint_port"],
            self._endpoint_fields["endpoint_path"],
        )

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(self._embeddings_url, json=payload)
                response.raise_for_status()
        except httpx.TimeoutException as exc:
            logger.error(
                "llm.request.failed provider=%s model=%s profile=%s reason=timeout "
                "input_count=%d elapsed_ms=%d endpoint_host=%s endpoint_port=%s endpoint_path=%s",
                self._provider_name,
                self._model,
                profile,
                len(texts),
                int((perf_counter() - started_at) * 1000),
                self._endpoint_fields["endpoint_host"],
                self._endpoint_fields["endpoint_port"],
                self._endpoint_fields["endpoint_path"],
            )
            raise EmbeddingError("Timeout calling LM Studio embeddings endpoint") from exc
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            logger.error(
                "llm.request.failed provider=%s model=%s profile=%s reason=http_status "
                "status_code=%s input_count=%d elapsed_ms=%d endpoint_host=%s endpoint_port=%s endpoint_path=%s",
                self._provider_name,
                self._model,
                profile,
                status_code,
                len(texts),
                int((perf_counter() - started_at) * 1000),
                self._endpoint_fields["endpoint_host"],
                self._endpoint_fields["endpoint_port"],
                self._endpoint_fields["endpoint_path"],
            )
            raise EmbeddingError(
                f"LM Studio returned HTTP {status_code} for embeddings request"
            ) from exc
        except httpx.RequestError as exc:
            logger.error(
                "llm.request.failed provider=%s model=%s profile=%s reason=request_error "
                "error_type=%s input_count=%d elapsed_ms=%d endpoint_host=%s endpoint_port=%s endpoint_path=%s",
                self._provider_name,
                self._model,
                profile,
                type(exc).__name__,
                len(texts),
                int((perf_counter() - started_at) * 1000),
                self._endpoint_fields["endpoint_host"],
                self._endpoint_fields["endpoint_port"],
                self._endpoint_fields["endpoint_path"],
            )
            raise EmbeddingError("Failed to reach LM Studio embeddings endpoint") from exc

        try:
            data = response.json()
        except Exception as exc:
            logger.error(
                "llm.request.failed provider=%s model=%s profile=%s reason=invalid_json "
                "status_code=%d input_count=%d elapsed_ms=%d endpoint_host=%s endpoint_port=%s endpoint_path=%s",
                self._provider_name,
                self._model,
                profile,
                response.status_code,
                len(texts),
                int((perf_counter() - started_at) * 1000),
                self._endpoint_fields["endpoint_host"],
                self._endpoint_fields["endpoint_port"],
                self._endpoint_fields["endpoint_path"],
            )
            raise EmbeddingError("Failed to parse LM Studio response") from exc

        if "data" not in data:
            logger.error(
                "llm.request.failed provider=%s model=%s profile=%s "
                "reason=missing_data_field status_code=%d input_count=%d elapsed_ms=%d "
                "endpoint_host=%s endpoint_port=%s endpoint_path=%s",
                self._provider_name,
                self._model,
                profile,
                response.status_code,
                len(texts),
                int((perf_counter() - started_at) * 1000),
                self._endpoint_fields["endpoint_host"],
                self._endpoint_fields["endpoint_port"],
                self._endpoint_fields["endpoint_path"],
            )
            raise EmbeddingError("Invalid response: missing 'data' field")

        try:
            embeddings = [item["embedding"] for item in data["data"]]
        except (KeyError, TypeError) as exc:
            logger.error(
                "llm.request.failed provider=%s model=%s profile=%s "
                "reason=invalid_embedding_structure status_code=%d input_count=%d elapsed_ms=%d "
                "endpoint_host=%s endpoint_port=%s endpoint_path=%s",
                self._provider_name,
                self._model,
                profile,
                response.status_code,
                len(texts),
                int((perf_counter() - started_at) * 1000),
                self._endpoint_fields["endpoint_host"],
                self._endpoint_fields["endpoint_port"],
                self._endpoint_fields["endpoint_path"],
            )
            raise EmbeddingError(f"Invalid embedding structure: {exc}") from exc

        # Validate dimensions
        for i, emb in enumerate(embeddings):
            if len(emb) != self._dimensions:
                logger.error(
                    "llm.request.failed provider=%s model=%s profile=%s "
                    "reason=dimension_mismatch expected_dims=%d actual_dims=%d text_index=%d "
                    "status_code=%d input_count=%d elapsed_ms=%d endpoint_host=%s endpoint_port=%s endpoint_path=%s",
                    self._provider_name,
                    self._model,
                    profile,
                    self._dimensions,
                    len(emb),
                    i,
                    response.status_code,
                    len(texts),
                    int((perf_counter() - started_at) * 1000),
                    self._endpoint_fields["endpoint_host"],
                    self._endpoint_fields["endpoint_port"],
                    self._endpoint_fields["endpoint_path"],
                )
                raise EmbeddingError(
                    f"Dimension mismatch: expected {self._dimensions}, "
                    f"got {len(emb)} for text index {i}"
                )

        logger.info(
            "llm.request.finished provider=%s model=%s profile=%s input_count=%d "
            "vector_count=%d dims=%d status_code=%d elapsed_ms=%d endpoint_host=%s "
            "endpoint_port=%s endpoint_path=%s",
            self._provider_name,
            self._model,
            profile,
            len(texts),
            len(embeddings),
            self._dimensions,
            response.status_code,
            int((perf_counter() - started_at) * 1000),
            self._endpoint_fields["endpoint_host"],
            self._endpoint_fields["endpoint_port"],
            self._endpoint_fields["endpoint_path"],
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

    @property
    def endpoint(self) -> str:
        """Return the canonical LM Studio API base URL."""
        return self._endpoint
