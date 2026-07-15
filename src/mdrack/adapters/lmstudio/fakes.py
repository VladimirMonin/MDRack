"""Offline deterministic fakes for LM Studio-facing ports."""

from __future__ import annotations

from mdrack.domain.profiles import EmbeddingCapabilities
from mdrack.embeddings.fake import FakeEmbeddingProvider
from mdrack.ports.model_catalog import ModelDescriptor
from mdrack.ports.model_lifecycle import LoadedModel
from mdrack.ports.reranker import (
    RerankDocument,
    RerankerError,
    RerankerUnavailable,
    RerankScore,
)


class DeterministicReranker:
    def __init__(
        self,
        *,
        score_by_candidate: dict[str, float] | None = None,
        mode: str = "success",
    ) -> None:
        self.score_by_candidate = score_by_candidate or {}
        self.mode = mode

    async def rerank(
        self,
        query: str,
        documents: list[RerankDocument],
        *,
        top_n: int,
    ) -> list[RerankScore]:
        del query
        if self.mode == "unsupported":
            raise RerankerUnavailable()
        if self.mode == "failure":
            raise RerankerError("provider_error")
        scored = [
            RerankScore(document.candidate_id, self.score_by_candidate.get(document.candidate_id, 0.0))
            for document in documents
        ]
        scored.sort(key=lambda item: (-item.score, item.candidate_id))
        return scored[:top_n]


class FakeModelCatalogProvider:
    def __init__(self, models: list[ModelDescriptor] | None = None) -> None:
        self.models = models or []

    async def list_models(self) -> list[ModelDescriptor]:
        return list(self.models)

    async def embedding_capabilities(self, model_key: str) -> EmbeddingCapabilities:
        del model_key
        return EmbeddingCapabilities()

    async def supports_reranking(self, model_key: str) -> bool | None:
        del model_key
        return None


class FakeModelLifecycleProvider:
    def __init__(self) -> None:
        self.loaded: dict[str, LoadedModel] = {}

    async def list_loaded_models(self) -> list[LoadedModel]:
        return sorted(self.loaded.values(), key=lambda model: model.key)

    async def load_model(self, model_key: str) -> LoadedModel:
        loaded = LoadedModel(model_key, f"fake:{model_key}", "loaded")
        self.loaded[loaded.instance_id or model_key] = loaded
        return loaded

    async def unload_model(self, instance_id: str) -> None:
        self.loaded.pop(instance_id, None)


__all__ = [
    "DeterministicReranker",
    "FakeEmbeddingProvider",
    "FakeModelCatalogProvider",
    "FakeModelLifecycleProvider",
]
