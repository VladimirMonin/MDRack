"""Application services exposed independently from CLI adapters."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mdrack.application.manifest import (
    MANIFEST_CONTRACT,
    MANIFEST_VERSION,
    ManifestError,
    ManifestErrorCode,
    PreparedResourceFacade,
    decode_prepared_resource_manifest,
    import_manifest,
    index_prepared_resource,
)

if TYPE_CHECKING:
    from mdrack.application.indexing import IndexingService


def __getattr__(name: str) -> Any:
    """Keep the legacy indexing export lazy so manifest imports stay lightweight."""
    if name == "IndexingService":
        from mdrack.application.indexing import IndexingService

        return IndexingService
    raise AttributeError(name)

__all__ = [
    "MANIFEST_CONTRACT",
    "MANIFEST_VERSION",
    "IndexingService",
    "ManifestError",
    "ManifestErrorCode",
    "PreparedResourceFacade",
    "decode_prepared_resource_manifest",
    "import_manifest",
    "index_prepared_resource",
]
