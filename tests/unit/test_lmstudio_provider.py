"""Unit tests for the LM Studio embedding provider."""

from __future__ import annotations

import asyncio
import json
import logging
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

from mdrack.adapters.sqlite.canonical_catalog import open_application_catalog, open_application_catalog_readonly
from mdrack.config.models import MDRackConfig
from mdrack.embeddings.lmstudio import (
    LMStudioControlClient,
    LMStudioControlError,
    LMStudioProvider,
)
from mdrack.embeddings.protocol import EmbeddingError, EmbeddingHealth
from mdrack.ingestion.images import (
    ExtractedImageText,
    ImageEmbeddingSpace,
    ImageIngestionService,
    StaticImageExtractor,
)
from mdrack.integrations.lmstudio import client as lm


class _AsyncClientStub:
    def __init__(self, response: MagicMock | None = None, side_effect: Exception | None = None) -> None:
        self._response = response
        self._side_effect = side_effect
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.requests: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    async def __aenter__(self) -> _AsyncClientStub:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False

    async def get(self, *args: object, **kwargs: object) -> MagicMock:
        self.calls.append((args, kwargs))
        self.requests.append(("GET", args, kwargs))
        if self._side_effect is not None:
            raise self._side_effect
        assert self._response is not None
        return self._response

    async def post(self, *args: object, **kwargs: object) -> MagicMock:
        self.calls.append((args, kwargs))
        self.requests.append(("POST", args, kwargs))
        if self._side_effect is not None:
            raise self._side_effect
        assert self._response is not None
        return self._response


def _patch_async_client(
    monkeypatch: pytest.MonkeyPatch,
    response: MagicMock | None = None,
    side_effect: Exception | None = None,
) -> list[_AsyncClientStub]:
    clients: list[_AsyncClientStub] = []

    def _factory(*args: object, **kwargs: object) -> _AsyncClientStub:
        del args, kwargs
        client = _AsyncClientStub(response=response, side_effect=side_effect)
        clients.append(client)
        return client

    monkeypatch.setattr("httpx.AsyncClient", _factory)
    return clients


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
    async def test_embed_single_text(self, provider: LMStudioProvider, monkeypatch: pytest.MonkeyPatch) -> None:
        """Embedding a single text should return a list with one vector."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"data": [{"embedding": [0.1] * 128, "index": 0}]}
        mock_response.raise_for_status = MagicMock()
        _patch_async_client(monkeypatch, response=mock_response)

        vectors = await provider.embed(["hello"])

        assert len(vectors) == 1
        assert len(vectors[0]) == 128
        assert all(v == 0.1 for v in vectors[0])

    @pytest.mark.asyncio
    async def test_embed_does_not_send_dimensions_without_explicit_request(
        self, provider: LMStudioProvider, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": [{"embedding": [0.1] * 128, "index": 0}]}
        mock_response.raise_for_status = MagicMock()
        clients = _patch_async_client(monkeypatch, response=mock_response)

        await provider.embed(["hello"])

        payload = clients[0].calls[0][1]["json"]
        assert "dimensions" not in payload
        assert provider.requested_dimensions is None
        assert provider.returned_dimensions == 128
        assert provider.vector_length_valid is True

    @pytest.mark.asyncio
    async def test_embed_sends_explicit_dimensions_only_with_tested_runtime_support(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        provider = LMStudioProvider(
            endpoint="http://localhost:1234",
            model="test-model",
            dimensions=64,
            requested_dimensions=64,
            dimensions_capability="tested",
            native_dimensions=1024,
        )
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": [{"embedding": [0.1] * 64, "index": 0}]}
        mock_response.raise_for_status = MagicMock()
        clients = _patch_async_client(monkeypatch, response=mock_response)

        vectors = await provider.embed(["hello"])

        assert clients[0].calls[0][1]["json"]["dimensions"] == 64
        assert len(vectors[0]) == 64
        assert provider.returned_dimensions == 64
        assert provider.mrl_status == "tested"

    @pytest.mark.asyncio
    async def test_matching_dimensions_without_native_dimension_do_not_claim_mrl(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        provider = LMStudioProvider(
            endpoint="http://localhost:1234",
            model="test-model",
            dimensions=64,
            requested_dimensions=64,
            dimensions_capability="tested",
        )
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": [{"embedding": [0.1] * 64, "index": 0}]}
        mock_response.raise_for_status = MagicMock()
        _patch_async_client(monkeypatch, response=mock_response)

        await provider.embed(["hello"])

        assert provider.mrl_status == "unsupported_by_runtime"

    @pytest.mark.asyncio
    async def test_embed_rejects_requested_dimensions_without_runtime_evidence(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        provider = LMStudioProvider(
            endpoint="http://localhost:1234",
            model="test-model",
            dimensions=64,
            requested_dimensions=64,
            dimensions_capability="not_tested",
        )
        clients = _patch_async_client(monkeypatch)

        with pytest.raises(EmbeddingError, match="requested_dimensions_not_tested"):
            await provider.embed(["hello"])

        assert clients == []
        assert provider.mrl_status == "unsupported_by_runtime"

    @pytest.mark.asyncio
    async def test_embed_multiple_texts(self, provider: LMStudioProvider, monkeypatch: pytest.MonkeyPatch) -> None:
        """Embedding multiple texts should return one vector per text."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": [
                {"embedding": [0.1] * 128, "index": 0},
                {"embedding": [0.2] * 128, "index": 1},
            ]
        }
        mock_response.raise_for_status = MagicMock()
        _patch_async_client(monkeypatch, response=mock_response)

        vectors = await provider.embed(["hello", "world"])

        assert len(vectors) == 2
        assert len(vectors[0]) == 128
        assert len(vectors[1]) == 128

    @pytest.mark.asyncio
    async def test_embed_splits_large_input_without_reordering(
        self, provider: LMStudioProvider, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.side_effect = [
            {"data": [{"embedding": [float(index)] * 128, "index": index} for index in range(start, stop)]}
            for start, stop in ((0, 32), (32, 64), (64, 65))
        ]
        clients = _patch_async_client(monkeypatch, response=mock_response)

        texts = [f"text-{index}" for index in range(65)]
        vectors = await provider.embed(texts)

        assert len(clients) == 3
        batches: list[object] = []
        for client in clients:
            payload = client.calls[0][1]["json"]
            assert isinstance(payload, dict)
            batches.append(payload["input"])
        assert batches == [texts[:32], texts[32:64], texts[64:]]
        assert [vector[0] for vector in vectors] == [float(index) for index in range(65)]

    @pytest.mark.asyncio
    async def test_embed_normalizes_base_endpoint(
        self, provider: LMStudioProvider, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Base endpoint input should produce a single /v1/embeddings path."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"data": [{"embedding": [0.1] * 128, "index": 0}]}
        mock_response.raise_for_status = MagicMock()
        clients = _patch_async_client(monkeypatch, response=mock_response)

        await provider.embed(["hello"])

        assert clients[0].calls[0][0][0] == "http://localhost:1234/v1/embeddings"

    @pytest.mark.asyncio
    async def test_embed_normalizes_v1_endpoint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An endpoint ending in /v1 should not become /v1/v1/embeddings."""
        provider = LMStudioProvider(
            endpoint="http://localhost:1234/v1",
            model="test-model",
            dimensions=128,
            timeout=10,
        )
        mock_response = MagicMock()
        mock_response.json.return_value = {"data": [{"embedding": [0.1] * 128, "index": 0}]}
        mock_response.raise_for_status = MagicMock()
        clients = _patch_async_client(monkeypatch, response=mock_response)

        await provider.embed(["hello"])

        assert clients[0].calls[0][0][0] == "http://localhost:1234/v1/embeddings"

    def test_embed_can_run_across_multiple_event_loops(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Repeated asyncio.run calls should not reuse a closed loop-bound client."""
        provider = LMStudioProvider(
            endpoint="http://localhost:1234/v1",
            model="test-model",
            dimensions=128,
            timeout=10,
        )
        mock_response = MagicMock()
        mock_response.json.return_value = {"data": [{"embedding": [0.1] * 128, "index": 0}]}
        mock_response.raise_for_status = MagicMock()
        clients = _patch_async_client(monkeypatch, response=mock_response)

        asyncio.run(provider.embed(["hello"]))
        asyncio.run(provider.embed(["world"]))

        assert len(clients) == 2
        assert clients[0].calls[0][1]["json"]["input"] == ["hello"]
        assert clients[1].calls[0][1]["json"]["input"] == ["world"]

    @pytest.mark.asyncio
    async def test_embed_timeout_raises_error(
        self, provider: LMStudioProvider, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Timeout should raise EmbeddingError."""
        _patch_async_client(
            monkeypatch,
            side_effect=httpx.TimeoutException("timeout"),
        )

        with pytest.raises(EmbeddingError, match="Timeout"):
            await provider.embed(["hello"])

    @pytest.mark.asyncio
    async def test_embed_connection_error_raises_error(
        self, provider: LMStudioProvider, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Connection error should raise EmbeddingError."""
        _patch_async_client(
            monkeypatch,
            side_effect=httpx.ConnectError("connection refused"),
        )

        with pytest.raises(EmbeddingError, match="Failed to reach"):
            await provider.embed(["hello"])

    @pytest.mark.asyncio
    async def test_embed_http_status_error_raises_error(
        self, provider: LMStudioProvider, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """HTTP status error should raise EmbeddingError."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500", request=MagicMock(), response=MagicMock(status_code=500)
        )
        _patch_async_client(monkeypatch, response=mock_response)

        with pytest.raises(EmbeddingError, match="HTTP 500"):
            await provider.embed(["hello"])

    @pytest.mark.asyncio
    async def test_embed_dimension_mismatch_raises_error(
        self, provider: LMStudioProvider, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Dimension mismatch should raise EmbeddingError."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": [{"embedding": [0.1] * 64, "index": 0}]  # Wrong dimension
        }
        mock_response.raise_for_status = MagicMock()
        _patch_async_client(monkeypatch, response=mock_response)

        with pytest.raises(EmbeddingError, match="Dimension mismatch"):
            await provider.embed(["hello"])

        assert provider.returned_dimensions == 64
        assert provider.vector_length_valid is False

    @pytest.mark.asyncio
    async def test_embed_invalid_response_missing_data(
        self, provider: LMStudioProvider, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Missing 'data' field should raise EmbeddingError."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"object": "list"}
        mock_response.raise_for_status = MagicMock()
        _patch_async_client(monkeypatch, response=mock_response)

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
    async def test_embed_query_adds_prefix(self, provider: LMStudioProvider, monkeypatch: pytest.MonkeyPatch) -> None:
        """embed_query should add retrieval prefix to the text."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"data": [{"embedding": [0.1] * 128, "index": 0}]}
        mock_response.raise_for_status = MagicMock()
        clients = _patch_async_client(monkeypatch, response=mock_response)

        await provider.embed_query("hello")

        # Verify the payload sent to the API
        payload = clients[0].calls[0][1]["json"]
        assert payload["input"] == ["Represent the query for retrieval: hello"]

    @pytest.mark.asyncio
    async def test_embed_query_uses_the_configured_instruction(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        provider = LMStudioProvider(
            endpoint="http://localhost:1234",
            model="test-model",
            dimensions=128,
            query_instruction="Search vector",
        )
        mock_response = MagicMock()
        mock_response.json.return_value = {"data": [{"embedding": [0.1] * 128, "index": 0}]}
        mock_response.raise_for_status = MagicMock()
        clients = _patch_async_client(monkeypatch, response=mock_response)

        await provider.embed_query("hello")

        payload = clients[0].calls[0][1]["json"]
        assert payload["input"] == ["Search vector: hello"]

    @pytest.mark.asyncio
    async def test_embed_query_returns_single_vector(
        self, provider: LMStudioProvider, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """embed_query should return a single vector."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"data": [{"embedding": [0.1] * 128, "index": 0}]}
        mock_response.raise_for_status = MagicMock()
        _patch_async_client(monkeypatch, response=mock_response)

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
    async def test_health_success(self, provider: LMStudioProvider, monkeypatch: pytest.MonkeyPatch) -> None:
        """Health check should return ok=True when API responds."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"data": [{"embedding": [0.1] * 128, "index": 0}]}
        mock_response.raise_for_status = MagicMock()
        _patch_async_client(monkeypatch, response=mock_response)

        health = await provider.health()

        assert isinstance(health, EmbeddingHealth)
        assert health.ok is True
        assert health.provider == "lmstudio"
        assert health.model == "test-model"
        assert health.dimensions == 128
        assert health.error is None

    @pytest.mark.asyncio
    async def test_health_failure(self, provider: LMStudioProvider, monkeypatch: pytest.MonkeyPatch) -> None:
        """Health check should return ok=False when API fails."""
        _patch_async_client(
            monkeypatch,
            side_effect=httpx.ConnectError("connection refused"),
        )

        health = await provider.health()

        assert isinstance(health, EmbeddingHealth)
        assert health.ok is False
        assert health.provider == "lmstudio"
        assert health.model == "test-model"
        assert health.dimensions == 128
        assert health.error is not None


class TestLMStudioControlClient:
    @pytest.fixture
    def client(self) -> LMStudioControlClient:
        return LMStudioControlClient(endpoint="http://localhost:1234/v1", timeout=10)

    def test_endpoint_property_returns_native_api_base(self) -> None:
        client = LMStudioControlClient(endpoint="http://localhost:1234/v1/embeddings")

        assert client.endpoint == "http://localhost:1234/api/v1"

    @pytest.mark.asyncio
    async def test_list_models_parses_loaded_instances(
        self, client: LMStudioControlClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": [
                {
                    "id": "Qwen/Qwen3-Embedding-0.6B-GGUF",
                    "state": "loaded",
                    "instances": [
                        {"instance_id": "inst-1"},
                        {"model_instance_id": "inst-2"},
                    ],
                },
                {
                    "id": "Qwen/Qwen3-Embedding-4B-GGUF",
                    "state": "not-loaded",
                },
            ]
        }
        mock_response.raise_for_status = MagicMock()
        clients = _patch_async_client(monkeypatch, response=mock_response)

        models = await client.list_models()

        assert len(models) == 2
        assert models[0].key == "Qwen/Qwen3-Embedding-0.6B-GGUF"
        assert models[0].loaded is True
        assert models[0].instance_ids == ("inst-1", "inst-2")
        assert models[1].loaded is False
        assert clients[0].requests[0][0] == "GET"
        assert clients[0].calls[0][0][0] == "http://localhost:1234/api/v1/models"

    @pytest.mark.asyncio
    async def test_list_loaded_models_filters_and_expands_instances(
        self, client: LMStudioControlClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "models": [
                {
                    "id": "Qwen/Qwen3-Embedding-0.6B-GGUF",
                    "loaded": True,
                    "instances": [{"instance_id": "inst-1"}],
                },
                {
                    "id": "Qwen/Qwen3-Embedding-4B-GGUF",
                    "loaded": False,
                },
            ]
        }
        mock_response.raise_for_status = MagicMock()
        _patch_async_client(monkeypatch, response=mock_response)

        loaded = await client.list_loaded_models()

        assert len(loaded) == 1
        assert loaded[0].key == "Qwen/Qwen3-Embedding-0.6B-GGUF"
        assert loaded[0].instance_id == "inst-1"
        assert loaded[0].state is None

    @pytest.mark.asyncio
    async def test_download_model_posts_request(
        self, client: LMStudioControlClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 202
        mock_response.json.return_value = {
            "status": "accepted",
            "download_id": "download-1",
        }
        mock_response.raise_for_status = MagicMock()
        clients = _patch_async_client(monkeypatch, response=mock_response)

        result = await client.download_model("Qwen/Qwen3-Embedding-0.6B-GGUF")

        assert result.key == "Qwen/Qwen3-Embedding-0.6B-GGUF"
        assert result.status == "accepted"
        assert result.download_id == "download-1"
        assert clients[0].requests[0][0] == "POST"
        assert clients[0].calls[0][0][0] == "http://localhost:1234/api/v1/models/download"
        assert clients[0].calls[0][1]["json"] == {"model": "Qwen/Qwen3-Embedding-0.6B-GGUF"}

    @pytest.mark.asyncio
    async def test_get_download_status_parses_progress(
        self, client: LMStudioControlClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "downloads": [
                {
                    "model": "Qwen/Qwen3-Embedding-0.6B-GGUF",
                    "status": "downloading",
                    "progress": "0.5",
                    "downloaded_bytes": "50",
                    "total_bytes": 100,
                }
            ]
        }
        mock_response.raise_for_status = MagicMock()
        _patch_async_client(monkeypatch, response=mock_response)

        downloads = await client.get_download_status()

        assert len(downloads) == 1
        assert downloads[0].key == "Qwen/Qwen3-Embedding-0.6B-GGUF"
        assert downloads[0].status == "downloading"
        assert downloads[0].progress == 0.5
        assert downloads[0].downloaded_bytes == 50
        assert downloads[0].total_bytes == 100

    @pytest.mark.asyncio
    async def test_load_model_supports_legacy_instance_id_field(
        self, client: LMStudioControlClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "model": "Qwen/Qwen3-Embedding-0.6B-GGUF",
            "status": "loaded",
            "model_instance_id": "inst-legacy",
        }
        mock_response.raise_for_status = MagicMock()
        clients = _patch_async_client(monkeypatch, response=mock_response)

        result = await client.load_model("Qwen/Qwen3-Embedding-0.6B-GGUF")

        assert result.key == "Qwen/Qwen3-Embedding-0.6B-GGUF"
        assert result.state == "loaded"
        assert result.instance_id == "inst-legacy"
        assert clients[0].calls[0][0][0] == "http://localhost:1234/api/v1/models/load"

    @pytest.mark.asyncio
    async def test_unload_model_posts_instance_id(
        self, client: LMStudioControlClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 204
        mock_response.raise_for_status = MagicMock()
        clients = _patch_async_client(monkeypatch, response=mock_response)

        await client.unload_model("inst-1")

        assert clients[0].requests[0][0] == "POST"
        assert clients[0].calls[0][0][0] == "http://localhost:1234/api/v1/models/unload"
        assert clients[0].calls[0][1]["json"] == {"instance_id": "inst-1"}

    @pytest.mark.asyncio
    async def test_dimension_probe_forwards_an_explicit_requested_dimension(
        self, client: LMStudioControlClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": [{"embedding": [0.25, 0.75]}]}
        mock_response.raise_for_status = MagicMock()
        clients = _patch_async_client(monkeypatch, response=mock_response)

        returned = await client.probe_embedding_dimensions(
            "Qwen/Qwen3-Embedding-0.6B-GGUF",
            dimensions=512,
        )

        assert returned == 2
        assert clients[0].calls[0][0][0] == "http://localhost:1234/v1/embeddings"
        assert clients[0].calls[0][1]["json"] == {
            "model": "Qwen/Qwen3-Embedding-0.6B-GGUF",
            "input": ["health check"],
            "dimensions": 512,
        }

    @pytest.mark.asyncio
    async def test_list_models_http_status_raises_control_error(
        self, client: LMStudioControlClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500", request=MagicMock(), response=MagicMock(status_code=500)
        )
        _patch_async_client(monkeypatch, response=mock_response)

        with pytest.raises(LMStudioControlError, match="HTTP 500"):
            await client.list_models()

    @pytest.mark.asyncio
    async def test_get_download_status_invalid_response_raises_control_error(
        self, client: LMStudioControlClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"downloads": "invalid"}
        mock_response.raise_for_status = MagicMock()
        _patch_async_client(monkeypatch, response=mock_response)

        with pytest.raises(LMStudioControlError, match="download status response"):
            await client.get_download_status()


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

    def test_endpoint_property_returns_canonical_api_base(self) -> None:
        """Endpoint metadata should use the canonical /v1 API base."""
        provider = LMStudioProvider(
            endpoint="http://localhost:1234",
            model="test-model",
            dimensions=256,
        )
        assert provider.endpoint == "http://localhost:1234/v1"


class TestLMStudioProviderClose:
    """Tests for close method."""

    @pytest.mark.asyncio
    async def test_close_is_a_safe_noop(self) -> None:
        """close() should succeed even though clients are request-scoped."""
        provider = LMStudioProvider(
            endpoint="http://localhost:1234",
            model="test-model",
            dimensions=128,
        )

        await provider.close()


@pytest.mark.parametrize("failed_event", ["llm.request.started", "llm.request.finished"])
def test_real_image_semantic_result_survives_lm_sink_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed_event: str,
) -> None:
    requests: list[httpx.Request] = []

    def response(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = json.loads(request.content)
        return httpx.Response(200, json={"data": [
            {"embedding": [0.25, 0.75], "index": i} for i in range(len(body["input"]))
        ]})

    original_client = httpx.AsyncClient
    monkeypatch.setattr(lm.httpx, "AsyncClient", lambda **kwargs: original_client(
        transport=httpx.MockTransport(response), **kwargs,
    ))
    provider = LMStudioProvider(
        endpoint="https://PRIVATE_HOST.invalid/private-api", model="fixture-model", dimensions=2,
    )
    source = tmp_path / "PRIVATE_PATH.png"
    source.write_bytes(b"\x89PNG\r\n\x1a\nsynthetic")
    logger = logging.getLogger(lm.__name__)
    previous = (logger.level, logger.propagate)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    good = logging.FileHandler(tmp_path / "lm.log", encoding="utf-8")
    logger.addHandler(good)

    class Broken(logging.Handler):
        calls = 0

        def emit(self, record: logging.LogRecord) -> None:
            if record.getMessage().startswith(failed_event + " "):
                self.calls += 1
                raise OSError("PRIVATE_EXCEPTION")

    broken = Broken()
    catalog = None
    try:
        catalog = open_application_catalog(tmp_path, MDRackConfig(), create=True)
        service = ImageIngestionService(
            catalog,
            extractor=StaticImageExtractor([ExtractedImageText("caption_text", "PRIVATE_CONTENT", "fixture-v1")]),
            text_embedding_provider=provider, text_space=ImageEmbeddingSpace("fixture-space", 2, "fixture-fingerprint"),
        )
        asyncio.run(service.ingest(source, resource_id="fixture-image", source_namespace="fixture",
                                   source_ref="images/fixture.png"))
        healthy = asyncio.run(service.search_semantic("PRIVATE_QUERY", limit=1))
        assert [item.resource_id for item in healthy.results] == ["fixture-image"]
        assert healthy.degraded is False
        before_requests = len(requests)
        logger.addHandler(broken)
        after = asyncio.run(service.search_semantic("PRIVATE_QUERY", limit=1))
        assert len(requests) == before_requests + 1
        assert broken.calls == 1
        assert after == healthy
        # Query recipe and vector transport remain real provider behavior.
        query_body = json.loads(requests[-1].content)
        assert query_body["input"] == ["Represent the query for retrieval: PRIVATE_QUERY"]
        good.flush()
        output = (tmp_path / "lm.log").read_text(encoding="utf-8")
        assert output
        assert "input_count=1" in output and "vector_count=1" in output and "elapsed_ms=" in output
        for private in ("PRIVATE_HOST", "private-api", "PRIVATE_PATH", "PRIVATE_CONTENT", "PRIVATE_QUERY",
                        "PRIVATE_EXCEPTION"):
            assert private not in output
    finally:
        if catalog is not None:
            catalog.close()
        logger.removeHandler(broken)
        logger.removeHandler(good)
        logger.setLevel(previous[0])
        logger.propagate = previous[1]
        good.close()
    reopened = open_application_catalog_readonly(tmp_path, MDRackConfig())
    try:
        counts = reopened.verify()
        assert (counts.resources, counts.representations, counts.units, counts.vectors) == (1, 2, 2, 2)
    finally:
        reopened.close()


@pytest.mark.parametrize("failure,reason", [
    ("capability", "requested_dimensions_not_tested"),
    ("timeout", "timeout"), ("http", "http_status"), ("request", "request_error"),
    ("json", "invalid_json"), ("missing", "missing_data_field"),
    ("structure", "invalid_embedding_structure"), ("dimensions", "dimension_mismatch"),
])
def test_real_provider_failure_is_not_replaced_by_failed_diagnostic_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str, reason: str,
) -> None:
    requests: list[httpx.Request] = []

    def response(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("PRIVATE_EXCEPTION", request=request)
        if failure == "request":
            raise httpx.ConnectError("PRIVATE_EXCEPTION", request=request)
        if failure == "http":
            return httpx.Response(503, text="PRIVATE_PROVIDER_BODY")
        if failure == "json":
            return httpx.Response(200, text="PRIVATE_PROVIDER_BODY")
        if failure == "missing":
            return httpx.Response(200, json={"private": "PRIVATE_PROVIDER_BODY"})
        if failure == "structure":
            return httpx.Response(200, json={"data": [{"private": "PRIVATE_PROVIDER_BODY"}]})
        return httpx.Response(200, json={"data": [{"embedding": [0.25], "index": 0}]})

    original_client = httpx.AsyncClient
    monkeypatch.setattr(lm.httpx, "AsyncClient", lambda **kwargs: original_client(
        transport=httpx.MockTransport(response), **kwargs,
    ))
    provider = LMStudioProvider(
        endpoint="https://PRIVATE_HOST.invalid/private-api", model="fixture-model", dimensions=2,
        requested_dimensions=1 if failure == "capability" else None,
    )
    logger = logging.getLogger(lm.__name__)
    previous = (logger.level, logger.propagate)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    good = logging.FileHandler(tmp_path / "failure.log", encoding="utf-8")
    logger.addHandler(good)

    class Broken(logging.Handler):
        calls = 0

        def emit(self, record: logging.LogRecord) -> None:
            if record.getMessage().startswith("llm.request.failed "):
                self.calls += 1
                raise OSError("PRIVATE_SINK_EXCEPTION")

    broken = Broken()
    try:
        with pytest.raises(EmbeddingError) as healthy:
            asyncio.run(provider.embed(["PRIVATE_CONTENT"]))
        logger.addHandler(broken)
        with pytest.raises(EmbeddingError) as after:
            asyncio.run(provider.embed(["PRIVATE_CONTENT"]))
        assert type(after.value) is type(healthy.value)
        assert str(after.value) == str(healthy.value)
        assert type(after.value.__cause__) is type(healthy.value.__cause__)
        assert broken.calls == 1
        assert len(requests) == (0 if failure == "capability" else 2)
        good.flush()
        output = (tmp_path / "failure.log").read_text(encoding="utf-8")
        assert output and "reason=" + reason in output
        assert ("calls_attempted=0" if failure == "capability" else "input_count=1") in output
        for private in ("PRIVATE_HOST", "private-api", "PRIVATE_PROVIDER_BODY", "PRIVATE_CONTENT",
                        "PRIVATE_EXCEPTION", "PRIVATE_SINK_EXCEPTION"):
            assert private not in output
    finally:
        logger.removeHandler(broken)
        logger.removeHandler(good)
        logger.setLevel(previous[0])
        logger.propagate = previous[1]
        good.close()


@pytest.mark.parametrize("edge", ["filter", "formatter", "emit"])
def test_provider_programming_errors_remain_visible(monkeypatch: pytest.MonkeyPatch, edge: str) -> None:
    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"data": [{"embedding": [0.25, 0.75]}]}))
    monkeypatch.setattr(lm.httpx, "AsyncClient", lambda **kwargs: original_client(transport=transport, **kwargs))
    provider = LMStudioProvider(endpoint="https://fixture.invalid", model="fixture-model", dimensions=2)
    logger = logging.getLogger(lm.__name__)
    previous = (logger.level, logger.propagate)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    class BrokenFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            raise ValueError("synthetic programming defect")

    class BrokenFormatter(logging.Formatter):
        def format(self, record: logging.LogRecord) -> str:
            raise ValueError("synthetic programming defect")

    class BrokenHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            raise ValueError("synthetic programming defect")

    handler = BrokenHandler() if edge == "emit" else logging.StreamHandler(StringIO())
    if edge == "filter":
        handler.addFilter(BrokenFilter())
    if edge == "formatter":
        handler.setFormatter(BrokenFormatter())
    logger.addHandler(handler)
    try:
        with pytest.raises(ValueError, match="programming defect"):
            asyncio.run(provider.embed(["PRIVATE_CONTENT"]))
    finally:
        logger.removeHandler(handler)
        handler.close()
        logger.setLevel(previous[0])
        logger.propagate = previous[1]
