"""Compatibility entry point for the CLI-independent indexing service."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from mdrack.adapters.sqlite.index_storage import create_sqlite_index_storage
from mdrack.application.indexing import IndexingService


@dataclass
class IndexerResult:
    """Backward-compatible index result with Phase 2 status fields."""

    run_id: str
    files_seen: int = 0
    files_changed: int = 0
    files_deleted: int = 0
    chunks_created: int = 0
    errors_count: int = 0
    status: str = "success"
    files_indexed: int = 0
    files_failed: int = 0
    error_codes: tuple[str, ...] = ()


def run_indexer(
    root: Path,
    config,
    provider: object | None = None,
    profile: str = "default",
    force_reindex: bool = False,
) -> IndexerResult:
    """Compose the default SQLite adapter and run the application service."""
    storage = create_sqlite_index_storage(root, config)
    service = IndexingService(
        root,
        config,
        storage,
        provider=provider,
        profile=profile,
    )
    try:
        result = service.scan(force_reindex=force_reindex)
        return IndexerResult(
            run_id=result.run_id,
            files_seen=result.files_seen,
            files_changed=result.files_changed,
            files_deleted=result.files_deleted,
            chunks_created=result.chunks_created,
            errors_count=result.errors_count,
            status=result.status,
            files_indexed=result.files_indexed,
            files_failed=result.files_failed,
            error_codes=result.error_codes,
        )
    finally:
        service.close()
