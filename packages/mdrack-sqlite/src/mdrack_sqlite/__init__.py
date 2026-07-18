"""Standalone SQLite catalog adapter for :mod:`mdrack_core` ports."""

from mdrack_sqlite.catalog import SQLiteCatalog, SQLiteVerification
from mdrack_sqlite.contract import (
    SQLITE_BRIDGE_SCHEMA_ID,
    SQLITE_CATALOG_API_VERSION,
    SQLITE_CATALOG_SCHEMA_ID,
    SQLITE_CATALOG_SCHEMA_VERSION,
    SQLITE_MIGRATION_MANIFEST,
    SQLITE_MIGRATION_MANIFEST_DIGEST,
)
from mdrack_sqlite.errors import SQLiteCatalogError, SQLiteErrorCode
from mdrack_sqlite.migrations import SQLiteMigrationError
from mdrack_sqlite.resource_store import SQLiteResourceStore

__all__ = [
    "SQLITE_BRIDGE_SCHEMA_ID",
    "SQLITE_CATALOG_API_VERSION",
    "SQLITE_CATALOG_SCHEMA_ID",
    "SQLITE_CATALOG_SCHEMA_VERSION",
    "SQLITE_MIGRATION_MANIFEST",
    "SQLITE_MIGRATION_MANIFEST_DIGEST",
    "SQLiteCatalog",
    "SQLiteCatalogError",
    "SQLiteErrorCode",
    "SQLiteMigrationError",
    "SQLiteResourceStore",
    "SQLiteVerification",
]
