"""Prepared input adapters; LM Studio HTTP currently advertises text only."""
from __future__ import annotations

from mdrack.application.textual_embedding_space import CanonicalTextEmbeddingSpace
from mdrack.domain.embedding_inputs import (
    EmbeddingResult,
    EncodingRole,
    InputCapability,
    PreparedInput,
    RouteCapabilities,
    TextInput,
)
from mdrack.domain.profiles import EmbeddingProfile
from mdrack.ports.embeddings import EmbeddingError, EmbeddingProvider


class LegacyTextEmbeddingAdapter:
    def __init__(self, provider: EmbeddingProvider, profile: EmbeddingProfile) -> None:
        self.provider = provider
        self.profile = profile
        self.space = CanonicalTextEmbeddingSpace(profile).record
        self.capabilities = RouteCapabilities(tuple(
            InputCapability(role, "text", ("text",), 8 * 1024 * 1024)
            for role in (EncodingRole.DOCUMENT, EncodingRole.QUERY)
        ))

    async def encode(self, value: PreparedInput, role: EncodingRole) -> EmbeddingResult:
        self.capabilities.validate(value, role)
        assert isinstance(value, TextInput)
        try:
            if role is EncodingRole.QUERY:
                vector = await self.provider.embed_query(value.text, profile=self.profile.name)
            else:
                vectors = await self.provider.embed([value.text], profile=self.profile.name)
                if len(vectors) != 1:
                    raise ValueError("embedding_count_mismatch")
                vector = vectors[0]
            return EmbeddingResult(tuple(vector), self.space)
        except Exception:
            raise EmbeddingError("embedding_provider_error") from None
