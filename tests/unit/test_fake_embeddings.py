"""Unit tests for the fake embedding provider."""

from __future__ import annotations

from mdrack.embeddings.fake import FakeEmbeddingProvider
from mdrack.embeddings.protocol import EmbeddingProvider


class TestFakeEmbeddingProviderDeterminism:
    """Tests for deterministic output from fake embedding provider."""

    def test_same_text_same_vector(self) -> None:
        """Same text should always produce the same vector."""
        provider = FakeEmbeddingProvider()
        text = "Hello, world!"

        vector1 = provider._text_to_vector(text)
        vector2 = provider._text_to_vector(text)

        assert vector1 == vector2

    def test_different_text_different_vectors(self) -> None:
        """Different texts should produce different vectors."""
        provider = FakeEmbeddingProvider()

        vector1 = provider._text_to_vector("Hello")
        vector2 = provider._text_to_vector("World")

        assert vector1 != vector2

    def test_vector_length_matches_dimensions(self) -> None:
        """Vector length should match configured dimensions."""
        dimensions = 64
        provider = FakeEmbeddingProvider(dimensions=dimensions)
        text = "Test text"

        vector = provider._text_to_vector(text)

        assert len(vector) == dimensions


class TestFakeEmbeddingProviderBatchEmbed:
    """Tests for batch embedding functionality."""

    def test_batch_embed_returns_correct_count(self) -> None:
        """Batch embed should return one vector per input text."""
        provider = FakeEmbeddingProvider()
        texts = ["Text one", "Text two", "Text three"]

        vectors = provider._text_to_vector_sync(texts)

        assert len(vectors) == len(texts)

    def test_batch_embed_vectors_are_lists(self) -> None:
        """Each vector in batch should be a list of floats."""
        provider = FakeEmbeddingProvider()
        texts = ["Text one", "Text two"]

        vectors = provider._text_to_vector_sync(texts)

        for vector in vectors:
            assert isinstance(vector, list)
            assert all(isinstance(v, float) for v in vector)

    def test_batch_embed_deterministic(self) -> None:
        """Batch embedding should be deterministic."""
        provider = FakeEmbeddingProvider()
        texts = ["Text one", "Text two"]

        vectors1 = provider._text_to_vector_sync(texts)
        vectors2 = provider._text_to_vector_sync(texts)

        assert vectors1 == vectors2


class TestFakeEmbeddingProviderQueryEmbed:
    """Tests for query embedding functionality."""

    def test_query_embed_returns_vector(self) -> None:
        """Query embedding should return a vector."""
        provider = FakeEmbeddingProvider()
        text = "Query text"

        vector = provider._text_to_vector(text)

        assert isinstance(vector, list)
        assert len(vector) == provider.dimensions

    def test_query_embed_deterministic(self) -> None:
        """Query embedding should be deterministic."""
        provider = FakeEmbeddingProvider()
        text = "Query text"

        vector1 = provider._text_to_vector(text)
        vector2 = provider._text_to_vector(text)

        assert vector1 == vector2


class TestFakeEmbeddingProviderHealth:
    """Tests for health check functionality."""

    def test_health_returns_ok_true(self) -> None:
        """Health check should always return ok=True."""
        provider = FakeEmbeddingProvider()

        health = provider._health_sync()

        assert health.ok is True

    def test_health_returns_provider_name(self) -> None:
        """Health check should return configured provider name."""
        provider_name = "test_provider"
        provider = FakeEmbeddingProvider(provider_name=provider_name)

        health = provider._health_sync()

        assert health.provider == provider_name

    def test_health_returns_model_name(self) -> None:
        """Health check should return model name."""
        provider = FakeEmbeddingProvider()

        health = provider._health_sync()

        assert health.model == "fake-hash-v1"

    def test_health_returns_dimensions(self) -> None:
        """Health check should return configured dimensions."""
        dimensions = 256
        provider = FakeEmbeddingProvider(dimensions=dimensions)

        health = provider._health_sync()

        assert health.dimensions == dimensions

    def test_health_returns_no_error(self) -> None:
        """Health check should have no error message."""
        provider = FakeEmbeddingProvider()

        health = provider._health_sync()

        assert health.error is None


class TestFakeEmbeddingProviderDimensions:
    """Tests for dimensions property."""

    def test_dimensions_returns_configured_value(self) -> None:
        """Dimensions property should return configured value."""
        dimensions = 512
        provider = FakeEmbeddingProvider(dimensions=dimensions)

        assert provider.dimensions == dimensions

    def test_dimensions_default_128(self) -> None:
        """Default dimensions should be 128."""
        provider = FakeEmbeddingProvider()

        assert provider.dimensions == 128

    def test_dimensions_various_values(self) -> None:
        """Dimensions property should work with various values."""
        for dim in [32, 64, 128, 256, 512, 1024]:
            provider = FakeEmbeddingProvider(dimensions=dim)
            assert provider.dimensions == dim


class TestFakeEmbeddingProviderProtocol:
    """Tests that FakeEmbeddingProvider implements the EmbeddingProvider protocol."""

    def test_implements_protocol(self) -> None:
        """FakeEmbeddingProvider should satisfy the EmbeddingProvider protocol."""
        provider = FakeEmbeddingProvider()
        assert isinstance(provider, EmbeddingProvider)

    def test_has_embed_method(self) -> None:
        """Provider should have async embed method."""
        provider = FakeEmbeddingProvider()
        assert hasattr(provider, "embed")
        assert callable(provider.embed)

    def test_has_embed_query_method(self) -> None:
        """Provider should have async embed_query method."""
        provider = FakeEmbeddingProvider()
        assert hasattr(provider, "embed_query")
        assert callable(provider.embed_query)

    def test_has_health_method(self) -> None:
        """Provider should have async health method."""
        provider = FakeEmbeddingProvider()
        assert hasattr(provider, "health")
        assert callable(provider.health)

    def test_has_dimensions_property(self) -> None:
        """Provider should have dimensions property."""
        provider = FakeEmbeddingProvider()
        assert hasattr(provider, "dimensions")
        assert isinstance(provider.dimensions, int)


class TestFakeEmbeddingProviderEdgeCases:
    """Tests for edge cases and special inputs."""

    def test_empty_text(self) -> None:
        """Should handle empty text."""
        provider = FakeEmbeddingProvider()

        vector = provider._text_to_vector("")

        assert isinstance(vector, list)
        assert len(vector) == provider.dimensions

    def test_unicode_text(self) -> None:
        """Should handle Unicode text."""
        provider = FakeEmbeddingProvider()
        texts = ["Привет", "こんにちは", "مرحبا", "Üniçödé"]

        for text in texts:
            vector = provider._text_to_vector(text)
            assert isinstance(vector, list)
            assert len(vector) == provider.dimensions

    def test_long_text(self) -> None:
        """Should handle very long text."""
        provider = FakeEmbeddingProvider()
        long_text = "word " * 10000

        vector = provider._text_to_vector(long_text)

        assert isinstance(vector, list)
        assert len(vector) == provider.dimensions

    def test_special_characters(self) -> None:
        """Should handle special characters."""
        provider = FakeEmbeddingProvider()
        text = "!@#$%^&*()_+-=[]{}|;':\",./<>?"

        vector = provider._text_to_vector(text)

        assert isinstance(vector, list)
        assert len(vector) == provider.dimensions

    def test_vector_values_in_range(self) -> None:
        """Vector values should be normalized (between -1 and 1)."""
        provider = FakeEmbeddingProvider()
        text = "Test text"

        vector = provider._text_to_vector(text)

        for v in vector:
            assert -1.0 <= v <= 1.0
