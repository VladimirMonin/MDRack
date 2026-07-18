"""Standalone SQLite catalog adapter for :mod:`mdrack_core` ports."""

from mdrack_sqlite.catalog import SQLiteCatalog, SQLiteVerification
from mdrack_sqlite.contract import SQLITE_BRIDGE_SCHEMA_ID, SQLITE_CATALOG_API_VERSION
from mdrack_sqlite.errors import SQLiteCatalogError, SQLiteErrorCode
from mdrack_sqlite.resource_store import SQLiteResourceStore

__all__ = [
    "SQLITE_BRIDGE_SCHEMA_ID",
    "SQLITE_CATALOG_API_VERSION",
    "SQLiteCatalog",
    "SQLiteCatalogError",
    "SQLiteErrorCode",
    "SQLiteResourceStore",
    "SQLiteVerification",
]
