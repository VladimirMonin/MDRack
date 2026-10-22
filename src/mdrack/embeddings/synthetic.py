"""Explicit deterministic offline contract provider, never production fallback."""
from __future__ import annotations

import hashlib

from mdrack.domain.embedding_inputs import (
    EmbeddingResult,
    EncodingRole,
    InputCapability,
    PreparedInput,
    RouteCapabilities,
    TextInput,
)
from mdrack_core import EmbeddingSpaceRecord


class SyntheticPreparedProvider:
    """No model quality claim. Identical prepared bytes produce identical vectors."""

    def __init__(self, dimensions: int = 8) -> None:
        self.space = EmbeddingSpaceRecord("synthetic-native-v1", dimensions, "cosine", "synthetic-native-v1",
                                         {"contract": "synthetic-native-v1"})
        self.capabilities = RouteCapabilities(tuple(
            InputCapability(role, modality, ("text", "image"), 32 * 1024 * 1024)
            for role in EncodingRole for modality in ("text", "image")
        ), reuse_document_vector=True)

    async def encode(self, value: PreparedInput, role: EncodingRole) -> EmbeddingResult:
        self.capabilities.validate(value, role)
        content = value.text.encode("utf-8") if isinstance(value, TextInput) else value.content
        digest = hashlib.sha256(content).digest()
        vector = tuple((digest[i % len(digest)] + 1) / 256 for i in range(self.space.dimensions))
        return EmbeddingResult(vector, self.space)


class SyntheticTemporalProvider(SyntheticPreparedProvider):
    """Explicit byte-hash fixture provider for temporal local-component/CLI tests only.

    This is not an audio/video model or a production fallback; scores have no
    semantic interpretation. The accepted image-only synthetic profile stays unchanged.
    """

    def __init__(self, dimensions: int = 8) -> None:
        self.space = EmbeddingSpaceRecord('synthetic-temporal-v1', dimensions, 'cosine', 'synthetic-temporal-v1',
            {'contract': 'synthetic-temporal-v1', 'vector_value_policy': 'ieee754-f32-canonical-v1',
             'vector_codec': 'ieee754-f32-le-v1'})
        self.capabilities = RouteCapabilities(tuple(
            InputCapability(role, modality, ('text', 'image', 'audio', 'video'), 16 * 1024 * 1024, 60_000)
            for role in EncodingRole for modality in ('text', 'image', 'audio', 'video')
        ), reuse_document_vector=True)
