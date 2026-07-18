"""Storage ports consumed by MDRack application services."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

from mdrack.domain.indexing import PreparedFile, SourceLocator
from mdrack.domain.retrieval import RetrievalCandidate
from mdrack_core.domain import SearchRequest, SearchResult


class ChangePlan(Protocol):
    new_files: list[Path]
    changed_files: list[Path]
    unchanged_files: list[Path]
    deleted_files: list[str]


class IndexStorage(Protocol):
    """Persistence contract required by the indexing application service."""

    def start_run(
        self,
        *,
        parser_name: str,
        parser_version: str,
        chunk_strategy_name: str,
        chunk_strategy_version: str,
    ) -> str: ...

    def plan_changes(self, scanned: list[Path], root: Path) -> Any: ...

    def get_file_by_path(self, relative_path: str) -> dict[str, Any] | None: ...

    def replace_file(self, prepared: PreparedFile) -> None: ...

    def delete_file(self, relative_path: str) -> None: ...

    def record_error(self, run_id: str, code: str, *, file_ref: str) -> None: ...

    def finish_run(self, run_id: str, *, status: str, stats: dict[str, int], error_codes: Sequence[str]) -> None: ...

    def get_chunk_source_locator(self, chunk_id: str) -> SourceLocator: ...

    def close(self) -> None: ...


class DocumentRepository(Protocol):
    def get_file_by_path(self, relative_path: str) -> dict[str, Any] | None: ...


class ChunkRepository(Protocol):
    def get_chunk_source_locator(self, chunk_id: str) -> SourceLocator: ...


class EmbeddingRepository(Protocol):
    def count_embeddings(self, profile_name: str) -> int: ...


class SearchIndex(Protocol):
    def search_text(self, query: str, *, limit: int, offset: int = 0) -> Any: ...


class RetrievalStorage(Protocol):
    """Normalized candidate access used by the application retrieval service."""

    def retrieve_text_candidates(
        self,
        query: str,
        *,
        limit: int,
        offset: int = 0,
    ) -> list[RetrievalCandidate]: ...

    def retrieve_semantic_candidates(
        self,
        query_vector: list[float],
        *,
        profile: str,
        profile_fingerprint: str | None,
        limit: int,
    ) -> list[RetrievalCandidate]: ...

    def search_core(self, request: SearchRequest) -> SearchResult: ...

    def resolve_embedding_space(
        self,
        profile: str,
        profile_fingerprint: str | None,
    ) -> str | None: ...


class ReadStorage(Protocol):
    def get_file_by_path(self, relative_path: str) -> dict[str, Any] | None: ...

    def get_chunk_source_locator(self, chunk_id: str) -> SourceLocator: ...

class KnowledgeStorage(IndexStorage, SearchIndex, RetrievalStorage, ReadStorage, Protocol):
    """Complete replaceable storage surface used by the embedded facade."""


class IndexRunRepository(Protocol):
    def start_run(
        self,
        *,
        parser_name: str,
        parser_version: str,
        chunk_strategy_name: str,
        chunk_strategy_version: str,
    ) -> str: ...
