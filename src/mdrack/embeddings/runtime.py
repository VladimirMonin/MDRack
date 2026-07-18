"""Shared runtime helpers for embedding providers and control clients."""

from __future__ import annotations

from inspect import isawaitable
from typing import Any

from mdrack.domain.profiles import EmbeddingProfile
from mdrack.embeddings.fake import FakeEmbeddingProvider
from mdrack.integrations.lmstudio import LMStudioControlClient, LMStudioProvider
from mdrack.ports.embeddings import EmbeddingProvider


def create_embedding_provider(provider_name: str, config: Any) -> EmbeddingProvider:
    """Create an embedding provider from resolved MDRack config."""
    if provider_name == "fake":
        return FakeEmbeddingProvider(
            dimensions=config.embedding.dimensions,
            provider_name="fake",
        )
    return LMStudioProvider(
        endpoint=config.embedding.endpoint,
        model=config.embedding.model,
        dimensions=config.embedding.dimensions,
        timeout=config.embedding.timeout_secs,
        requested_dimensions=config.embedding.requested_dimensions,
        dimensions_capability=config.embedding.dimensions_capability,
    )


def embedding_profile_from_config(
    config: Any,
    provider: object,
    profile_name: str = "default",
) -> EmbeddingProfile:
    """Build the complete vector identity used by indexing and retrieval."""
    provider_name = str(
        getattr(provider, "provider_name", getattr(provider, "_provider_name", config.embedding.provider))
    )
    model_name = str(
        getattr(provider, "model_name", getattr(provider, "_model_name", config.embedding.model))
    )
    dimensions = int(getattr(provider, "dimensions", config.embedding.dimensions))
    return EmbeddingProfile(
        name=profile_name,
        provider=provider_name,
        runtime=config.embedding.runtime if provider_name == "lmstudio" else "offline-test",
        model_key=model_name,
        model_family=config.embedding.model_family,
        quantization=config.embedding.quantization,
        output_dimensions=dimensions,
        query_instruction=config.embedding.query_instruction,
        normalization_mode=config.embedding.normalization_mode,
        endpoint_family=config.embedding.endpoint_family,
        instruction_profile=config.embedding.instruction_profile,
        schema_version=config.embedding.profile_schema_version,
    )


def create_lmstudio_control_client(config: Any) -> LMStudioControlClient:
    """Create the LM Studio native control client from resolved config."""
    return LMStudioControlClient(
        endpoint=config.embedding.endpoint,
        timeout=config.embedding.timeout_secs,
    )


async def close_async_resource(resource: object | None) -> None:
    """Safely close an async-capable resource if it exposes ``close``."""
    if resource is None:
        return
    close = getattr(resource, "close", None)
    if close is None:
        return
    result = close()
    if isawaitable(result):
        await result
