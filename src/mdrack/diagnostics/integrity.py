"""Store status and integrity diagnostics."""

from __future__ import annotations

import sqlite3

from mdrack_sqlite.contract_v2 import SQLITE_CATALOG_V2_SCHEMA_VERSION
from mdrack_sqlite.migrations_v2 import validate_v2_clean_identity


def get_resource_core_v2_status(conn: sqlite3.Connection) -> dict[str, object]:
    """Return contract-aware aggregate status for one verified v2 catalog.

    This reader deliberately never touches compatibility tables such as
    ``files`` or ``chunk_embeddings`` because a clean v2 catalog does not own
    them.
    """
    validate_v2_clean_identity(conn)

    def count(table: str) -> int:
        value = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("resource_core_count_invalid")
        return value

    resources_count = count("core_resources")
    representations_count = count("core_representations")
    units_count = count("core_search_units")
    vectors_count = count("core_unit_embeddings")
    embedding_spaces_count = count("core_embedding_spaces")
    fts_rows_count = count("core_search_units_fts")
    return {
        "contract_kind": "resource_core_v2",
        "files_count": resources_count,
        "chunks_count": units_count,
        "embeddings_count": vectors_count,
        "resources_count": resources_count,
        "representations_count": representations_count,
        "units_count": units_count,
        "vectors_count": vectors_count,
        "embedding_spaces_count": embedding_spaces_count,
        "fts_rows_count": fts_rows_count,
        "active_profile": "default",
        "profile_model": None,
        "profile_dimensions": None,
        "profile_endpoint": None,
        "schema_version": SQLITE_CATALOG_V2_SCHEMA_VERSION,
    }
