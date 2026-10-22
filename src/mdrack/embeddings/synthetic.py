"""Explicit deterministic offline contract provider, never production fallback."""
from __future__ import annotations

import hashlib

from mdrack.domain.embedding_inputs import (
    EmbeddingResult,
    EncodingRole,
    ImageInput,
    InputCapability,
    PreparedInput,
    RouteCapabilities,
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
        content = value.content if isinstance(value, ImageInput) else value.text.encode("utf-8")
        digest = hashlib.sha256(content).digest()
        vector = tuple((digest[i % len(digest)] + 1) / 256 for i in range(self.space.dimensions))
        return EmbeddingResult(vector, self.space)
