"""Unit tests for the LM Studio embedding provider."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from mdrack.embeddings.lmstudio import LMStudioProvider
from mdrack.embeddings.protocol import EmbeddingError, EmbeddingHealth


class TestLMStudioProviderEmbed:
    """Tests for embed method."""

    @pytest.fixture
    def provider(self) -> LMStudioProvider:
        """Create a provider with mocked HTTP client."""
        return LMStudioProvider(
            endpoint="http://localhost:1234",
            model="test-model",
            dimensions=128,
            timeout=10,
        )

    @pytest.mark.asyncio
    async def test_embed_single_text(self, provider: LMStudioProvider) -> None:
        """Embedding a single text should return a list with one vector."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": [{"embedding": [0.1] * 128, "index": 0}]
        }
        mock_response.raise_for_status = MagicMock()
        provider._client.post = AsyncMock(return_value=mock_response)

        vectors = await provider.embed(["hello"])

        assert len(vectors) == 1
        assert len(vectors[0]) == 128
        assert all(v == 0.1 for v in vectors[0])

    @pytest.mark.asyncio
    async def test_embed_multiple_texts(self, provider: LMStudioProvider) -> None:
        """Embedding multiple texts should return one vector per text."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": [
                {"embedding": [0.1] * 128, "index": 0},
                {"embedding": [0.2] * 128, "index": 1},
            ]
        }
        mock_response.raise_for_status = MagicMock()
        provider._client.post = AsyncMock(return_value=mock_response)

        vectors = await provider.embed(["hello", "world"])

        assert len(vectors) == 2
        assert len(vectors[0]) == 128
        assert len(vectors[1]) == 128

    @pytest.mark.asyncio
    async def test_embed_timeout_raises_error(self, provider: LMStudioProvider) -> None:
        """Timeout should raise EmbeddingError."""
        provider._client.post = AsyncMock(
            side_effect=httpx.TimeoutException("timeout")
        )

        with pytest.raises(EmbeddingError, match="Timeout"):
            await provider.embed(["hello"])

    @pytest.mark.asyncio
    async def test_embed_connection_error_raises_error(
        self, provider: LMStudioProvider
    ) -> None:
        """Connection error should raise EmbeddingError."""
        provider._client.post = AsyncMock(
            side_effect=httpx.ConnectError("connection refused")
        )

        with pytest.raises(EmbeddingError, match="Request error"):
            await provider.embed(["hello"])

    @pytest.mark.asyncio
    async def test_embed_http_status_error_raises_error(
        self, provider: LMStudioProvider
    ) -> None:
        """HTTP status error should raise EmbeddingError."""
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500", request=MagicMock(), response=MagicMock()
        )
        provider._client.post = AsyncMock(return_value=mock_response)

        with pytest.raises(EmbeddingError, match="HTTP error"):
            await provider.embed(["hello"])

    @pytest.mark.asyncio
    async def test_embed_dimension_mismatch_raises_error(
        self, provider: LMStudioProvider
    ) -> None:
        """Dimension mismatch should raise EmbeddingError."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": [{"embedding": [0.1] * 64, "index": 0}]  # Wrong dimension
        }
        mock_response.raise_for_status = MagicMock()
        provider._client.post = AsyncMock(return_value=mock_response)

        with pytest.raises(EmbeddingError, match="Dimension mismatch"):
            await provider.embed(["hello"])

    @pytest.mark.asyncio
    async def test_embed_invalid_response_missing_data(
        self, provider: LMStudioProvider
    ) -> None:
        """Missing 'data' field should raise EmbeddingError."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"object": "list"}
        mock_response.raise_for_status = MagicMock()
        provider._client.post = AsyncMock(return_value=mock_response)

        with pytest.raises(EmbeddingError, match="missing 'data' field"):
            await provider.embed(["hello"])


class TestLMStudioProviderEmbedQuery:
    """Tests for embed_query method."""

    @pytest.fixture
    def provider(self) -> LMStudioProvider:
        """Create a provider with mocked HTTP client."""
        return LMStudioProvider(
            endpoint="http://localhost:1234",
            model="test-model",
            dimensions=128,
            timeout=10,
        )

    @pytest.mark.asyncio
    async def test_embed_query_adds_prefix(self, provider: LMStudioProvider) -> None:
        """embed_query should add retrieval prefix to the text."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": [{"embedding": [0.1] * 128, "index": 0}]
        }
        mock_response.raise_for_status = MagicMock()
        provider._client.post = AsyncMock(return_value=mock_response)

        await provider.embed_query("hello")

        # Verify the payload sent to the API
        call_args = provider._client.post.call_args
        payload = call_args[1]["json"]
        assert payload["input"] == ["Represent this document for retrieval: hello"]

    @pytest.mark.asyncio
    async def test_embed_query_returns_single_vector(
        self, provider: LMStudioProvider
    ) -> None:
        """embed_query should return a single vector."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": [{"embedding": [0.1] * 128, "index": 0}]
        }
        mock_response.raise_for_status = MagicMock()
        provider._client.post = AsyncMock(return_value=mock_response)

        vector = await provider.embed_query("hello")

        assert isinstance(vector, list)
        assert len(vector) == 128


class TestLMStudioProviderHealth:
    """Tests for health method."""

    @pytest.fixture
    def provider(self) -> LMStudioProvider:
        """Create a provider with mocked HTTP client."""
        return LMStudioProvider(
            endpoint="http://localhost:1234",
            model="test-model",
            dimensions=128,
            timeout=10,
        )

    @pytest.mark.asyncio
    async def test_health_success(self, provider: LMStudioProvider) -> None:
        """Health check should return ok=True when API responds."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": [{"embedding": [0.1] * 128, "index": 0}]
        }
        mock_response.raise_for_status = MagicMock()
        provider._client.post = AsyncMock(return_value=mock_response)

        health = await provider.health()

        assert isinstance(health, EmbeddingHealth)
        assert health.ok is True
        assert health.provider == "lmstudio"
        assert health.model == "test-model"
        assert health.dimensions == 128
        assert health.error is None

    @pytest.mark.asyncio
    async def test_health_failure(self, provider: LMStudioProvider) -> None:
        """Health check should return ok=False when API fails."""
        provider._client.post = AsyncMock(
            side_effect=httpx.ConnectError("connection refused")
        )

        health = await provider.health()

        assert isinstance(health, EmbeddingHealth)
        assert health.ok is False
        assert health.provider == "lmstudio"
        assert health.model == "test-model"
        assert health.dimensions == 128
        assert health.error is not None


class TestLMStudioProviderDimensions:
    """Tests for dimensions property."""

    def test_dimensions_returns_configured_value(self) -> None:
        """Dimensions property should return configured value."""
        provider = LMStudioProvider(
            endpoint="http://localhost:1234",
            model="test-model",
            dimensions=256,
        )
        assert provider.dimensions == 256

    def test_dimensions_default(self) -> None:
        """Dimensions property should work with various values."""
        for dim in [64, 128, 256, 512]:
            provider = LMStudioProvider(
                endpoint="http://localhost:1234",
                model="test-model",
                dimensions=dim,
            )
            assert provider.dimensions == dim


class TestLMStudioProviderClose:
    """Tests for close method."""

    @pytest.mark.asyncio
    async def test_close_calls_client_aclose(self) -> None:
        """close() should call the HTTP client's aclose method."""
        provider = LMStudioProvider(
            endpoint="http://localhost:1234",
            model="test-model",
            dimensions=128,
        )
        provider._client.aclose = AsyncMock()

        await provider.close()

        provider._client.aclose.assert_called_once()
