"""Embedded MDRack engine for host applications."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mdrack.adapters.sqlite.index_storage import create_sqlite_index_storage
from mdrack.application.indexing import IndexingService
from mdrack.application.query import ReadService, SearchService
from mdrack.domain.indexing import IndexingResult, SourceLocator
from mdrack.ports.storage import KnowledgeStorage, ReadStorage, SearchIndex
from mdrack.search.text import TextSearchResult


class MDRackEngine:
    """Reusable Python facade that does not import Click or CLI modules."""

    def __init__(
        self,
        *,
        root: Path,
        config: Any,
        embedding_provider: object | None = None,
        profile: str = "default",
        root_id: str = "default",
        storage: KnowledgeStorage | None = None,
        search_index: SearchIndex | None = None,
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

    def search_text(self, query: str, *, limit: int = 20, offset: int = 0) -> TextSearchResult:
        return SearchService(self.search_index).search_text(query, limit=limit, offset=offset)

    def get_file_by_path(self, relative_path: str) -> dict[str, Any] | None:
        return ReadService(self.read_storage).get_file_by_path(relative_path)

    def get_chunk_source_locator(self, chunk_id: str) -> SourceLocator:
        return ReadService(self.read_storage).get_chunk_source_locator(chunk_id)

    def close(self) -> None:
        self.storage.close()

    def __enter__(self) -> MDRackEngine:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
