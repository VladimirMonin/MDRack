"""Privacy-safe lifecycle errors for the SQLite catalog."""

from __future__ import annotations

from enum import StrEnum


class SQLiteErrorCode(StrEnum):
    INVALID_PATH = "invalid_path"
    DATABASE_EXISTS = "database_exists"
    OPEN_FAILED = "open_failed"
    READ_ONLY_OPEN_FAILED = "readonly_open_failed"
    SCHEMA_MISMATCH = "schema_mismatch"
    MIGRATION_FAILED = "migration_failed"
    CLOSED = "closed"
    ACTIVE_TRANSACTION = "active_transaction"
    VERIFY_FAILED = "verify_failed"


class SQLiteCatalogError(RuntimeError):
    """A stable error carrying only a non-sensitive category."""

    def __init__(self, code: SQLiteErrorCode) -> None:
        if not isinstance(code, SQLiteErrorCode):
            raise TypeError("code must be SQLiteErrorCode")
        self.code = code
        super().__init__(code.value)
