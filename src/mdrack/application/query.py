"""CLI-independent query and read application services."""

from __future__ import annotations

from typing import Any

from mdrack.domain.indexing import SourceLocator
from mdrack.ports.storage import ReadStorage, SearchIndex


class SearchService:
    """Execute retrieval through an injected search port."""

    def __init__(self, search_index: SearchIndex) -> None:
        self.search_index = search_index

    def search_text(self, query: str, *, limit: int = 20, offset: int = 0) -> Any:
        return self.search_index.search_text(query, limit=limit, offset=offset)


class ReadService:
    """Resolve persisted documents and source locators through a read port."""

    def __init__(self, storage: ReadStorage) -> None:
        self.storage = storage

    def get_file_by_path(self, relative_path: str) -> dict[str, Any] | None:
        return self.storage.get_file_by_path(relative_path)

    def get_chunk_source_locator(self, chunk_id: str) -> SourceLocator:
        return self.storage.get_chunk_source_locator(chunk_id)
