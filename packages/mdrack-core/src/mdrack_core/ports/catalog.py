"""Catalog ports for provider- and persistence-neutral resource graphs."""

from __future__ import annotations

from typing import Protocol

from ..domain.batches import PreparedResourceBatch
from ..domain.resources import ResourceRecord, SearchUnitRecord
from ..domain.search import SearchScope
from ..domain.vectors import EmbeddingSpaceRecord, VectorRecord


class ResourceWritePort(Protocol):
    def replace_resource(self, batch: PreparedResourceBatch) -> None: ...

    def delete_resource(self, resource_id: str) -> None: ...


class ResourceReadPort(Protocol):
    def read_resource(self, resource_id: str) -> ResourceRecord | None: ...

    def read_unit(self, unit_id: str) -> SearchUnitRecord | None: ...

    def read_vector(self, unit_id: str, space_id: str) -> VectorRecord | None: ...

    def find_by_content_hash(
        self,
        content_hash: str,
        *,
        scope: SearchScope,
    ) -> list[ResourceRecord]: ...


class CatalogPort(ResourceWritePort, ResourceReadPort, Protocol):
    """Complete shared catalog surface used by core application services."""

    def resolve_embedding_space(
        self,
        *,
        fingerprint: str,
        dimensions: int,
    ) -> EmbeddingSpaceRecord | None: ...
