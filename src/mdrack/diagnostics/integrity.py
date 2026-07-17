"""Store status and integrity diagnostics."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from mdrack.adapters.sqlite.generation_runtime import SQLiteGenerationRuntime
from mdrack.application.generation_manager import StoreGenerationManager
from mdrack.storage.sqlite.migrations import get_applied_migrations
from mdrack.storage.sqlite.repositories import (
    count_chunks,
    count_embeddings,
    count_files,
)

logger = logging.getLogger(__name__)


def get_generation_status(store_dir: Path) -> dict[str, object]:
    """Return the privacy-safe generation projection used by status and doctor."""
    snapshot = StoreGenerationManager(
        store_dir,
        runtime=SQLiteGenerationRuntime(),
    ).status()
    generation_state = snapshot.active_state
    managed_generation_count = snapshot.generations_total + snapshot.corrupt_metadata_total
    if snapshot.pointer_status == "invalid" or (
        snapshot.pointer_status == "missing" and managed_generation_count > 0
    ):
        generation_state = "failed"
    elif generation_state is None:
        if snapshot.building_total:
            generation_state = "building"
        elif snapshot.failed_total:
            generation_state = "failed"
        elif snapshot.ready_total:
            generation_state = "rebuild_required"
        else:
            generation_state = "legacy_only"
    return {
        "generation_state": generation_state,
        "generation_pointer_status": snapshot.pointer_status,
        "generation_building_count": snapshot.building_total,
        "generation_ready_count": snapshot.ready_total,
        "generation_failed_count": snapshot.failed_total,
        "generation_corrupt_count": snapshot.corrupt_metadata_total,
        "generation_metadata_count": managed_generation_count,
    }


def get_store_status(conn: sqlite3.Connection, profile_name: str = "default") -> dict[str, object]:
    """Return a summary of the knowledge store.

    Args:
        conn: An open SQLite connection.

    Returns:
        Dict with files_count, chunks_count, embeddings_count,
        active_profile, schema_version, and active profile metadata when present.
    """
    files_count = count_files(conn)
    chunks_count = count_chunks(conn)
    embeddings_count = count_embeddings(conn, profile_name=profile_name)

    applied = get_applied_migrations(conn)
    schema_version = max(applied) if applied else None

    row = conn.execute(
        "SELECT model, dimensions, endpoint FROM embedding_profiles WHERE name = ?",
        (profile_name,),
    ).fetchone()

    return {
        "files_count": files_count,
        "chunks_count": chunks_count,
        "embeddings_count": embeddings_count,
        "active_profile": profile_name,
        "profile_model": row["model"] if row is not None else None,
        "profile_dimensions": row["dimensions"] if row is not None else None,
        "profile_endpoint": row["endpoint"] if row is not None else None,
        "schema_version": schema_version,
    }
