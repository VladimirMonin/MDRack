"""Shared runtime helpers for embedding providers and control clients."""

from __future__ import annotations

from inspect import isawaitable
from typing import Any

from mdrack.embeddings.fake import FakeEmbeddingProvider
from mdrack.embeddings.lmstudio import LMStudioControlClient, LMStudioProvider
from mdrack.embeddings.protocol import EmbeddingProvider


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
