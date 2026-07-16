"""Embedded MDRack engine for host applications."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mdrack.adapters.sqlite.index_storage import create_sqlite_index_storage
from mdrack.application.indexing import IndexingService
from mdrack.application.query import ReadService, SearchService
from mdrack.domain.indexing import IndexingResult, SourceLocator
from mdrack.domain.retrieval import RetrievalResult
from mdrack.embeddings.protocol import EmbeddingProvider
from mdrack.embeddings.runtime import embedding_profile_from_config
from mdrack.ports.storage import KnowledgeStorage, ReadStorage, RetrievalStorage


class MDRackEngine:
    """Reusable Python facade that does not import Click or CLI modules."""

    def __init__(
        self,
        *,
        root: Path,
        config: Any,
        embedding_provider: EmbeddingProvider | None = None,
        profile: str = "default",
        root_id: str = "default",
        storage: KnowledgeStorage | None = None,
        search_index: RetrievalStorage | None = None,
        read_storage: ReadStorage | None = None,
    ) -> None:
        self.root = root.resolve()
        self.config = config
        self.embedding_provider = embedding_provider
        self.profile = profile
        self.root_id = root_id
        if storage is None:
            storage = create_sqlite_index_storage(self.root, config)
        self.storage = storage
        self.search_index = search_index or storage
        self.read_storage = read_storage or storage
        self.search_service = SearchService(
            self.search_index,
            embedding_provider=self.embedding_provider,
            profile=self.profile,
            profile_fingerprint=(
                embedding_profile_from_config(config, self.embedding_provider, self.profile).fingerprint
                if self.embedding_provider is not None
                else None
            ),
            rrf_k=self.config.search.rrf_k,
        )

    def scan(self, *, force_reindex: bool = False) -> IndexingResult:
        service = IndexingService(
            self.root,
            self.config,
            self.storage,
            provider=self.embedding_provider,
            profile=self.profile,
            root_id=self.root_id,
        )
        return service.scan(force_reindex=force_reindex)

    def search_text(self, query: str, *, limit: int = 20, offset: int = 0) -> RetrievalResult:
        return self.search_service.search_text(query, limit=limit, offset=offset)

    async def search_semantic(self, query: str, *, limit: int = 20) -> RetrievalResult:
        return await self.search_service.search_semantic(query, limit=limit)

    async def search_hybrid(
        self,
        query: str,
        *,
        limit: int = 20,
        reranker: None = None,
    ) -> RetrievalResult:
        return await self.search_service.search_hybrid(query, limit=limit, reranker=reranker)

    def get_file_by_path(self, relative_path: str) -> dict[str, Any] | None:
        public_reader = getattr(self.read_storage, "get_public_file_by_path", None)
        if callable(public_reader):
            result = public_reader(relative_path)
            return result if isinstance(result, dict) or result is None else None
        return ReadService(self.read_storage).get_file_by_path(relative_path)

    def get_chunk(self, logical_id: str) -> dict[str, Any] | None:
        """Read one chunk by its public logical identity."""
        reader = getattr(self.read_storage, "get_chunk_by_logical_id", None)
        if not callable(reader):
            raise NotImplementedError("read storage does not support logical chunk reads")
        result = reader(logical_id)
        return result if isinstance(result, dict) or result is None else None

    def get_chunk_source_locator(self, chunk_id: str) -> SourceLocator:
        return ReadService(self.read_storage).get_chunk_source_locator(chunk_id)

    def close(self) -> None:
        self.storage.close()

    def __enter__(self) -> MDRackEngine:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
