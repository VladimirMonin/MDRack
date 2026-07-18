"""CLI-independent query and read application services."""

from __future__ import annotations

from typing import Any

from mdrack.application.retrieval import RetrievalService
from mdrack.domain.indexing import SourceLocator
from mdrack.domain.retrieval import RetrievalResult
from mdrack.ports.embeddings import EmbeddingProvider
from mdrack.ports.storage import ReadStorage, RetrievalStorage


class SearchService:
    """Compatibility facade over the canonical retrieval service."""

    def __init__(
        self,
        storage: RetrievalStorage,
        *,
        embedding_provider: EmbeddingProvider | None = None,
        profile: str = "default",
        profile_fingerprint: str | None = None,
        rrf_k: int = 60,
    ) -> None:
        self.retrieval = RetrievalService(
            storage,
            embedding_provider=embedding_provider,
            profile=profile,
            profile_fingerprint=profile_fingerprint,
            rrf_k=rrf_k,
        )

    def search_text(self, query: str, *, limit: int = 20, offset: int = 0) -> RetrievalResult:
        return self.retrieval.search_text(query, limit=limit, offset=offset)

    async def search_semantic(self, query: str, *, limit: int = 20) -> RetrievalResult:
        return await self.retrieval.search_semantic(query, limit=limit)

    async def search_hybrid(self, query: str, *, limit: int = 20, reranker: None = None) -> RetrievalResult:
        return await self.retrieval.search_hybrid(query, limit=limit, reranker=reranker)


class ReadService:
    """Resolve persisted documents and source locators through a read port."""

    def __init__(self, storage: ReadStorage) -> None:
        self.storage = storage

    def get_file_by_path(self, relative_path: str) -> dict[str, Any] | None:
        return self.storage.get_file_by_path(relative_path)

    def get_chunk_source_locator(self, chunk_id: str) -> SourceLocator:
        return self.storage.get_chunk_source_locator(chunk_id)
