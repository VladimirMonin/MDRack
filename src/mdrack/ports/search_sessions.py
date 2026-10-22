"""Optional app search capability, separate from legacy injected catalog ports.

A factory owns a new reader per operation. The borrowed handles are valid only
inside open(); consumers must finish all branches and eager DTO mapping there.
Provider preparation must finish before entry. No guarantee spans a later read.
"""
from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Protocol

from mdrack.ports.storage import RetrievalStorage
from mdrack_core.domain import EmbeddingSpaceRecord
from mdrack_core.ports.catalog import ResourceReadPort
from mdrack_core.ports.search import LexicalSearchPort, VectorSearchPort


class SearchCatalogReader(ResourceReadPort, LexicalSearchPort, VectorSearchPort, Protocol):
    """Read-only search capability; legacy CatalogPort is deliberately unchanged."""

    def resolve_embedding_space(
        self, *, fingerprint: str, dimensions: int,
    ) -> EmbeddingSpaceRecord | None: ...

    def resolve_embedding_spaces(
        self, *, fingerprint: str, dimensions: int,
    ) -> tuple[EmbeddingSpaceRecord, ...]: ...


@dataclass(frozen=True)
class SearchReadSession:
    catalog: SearchCatalogReader
    storage: RetrievalStorage


class SearchReadSessionFactory(Protocol):
    def open(self) -> AbstractContextManager[SearchReadSession]: ...
