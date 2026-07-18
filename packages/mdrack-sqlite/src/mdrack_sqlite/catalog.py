"""Context-managed lifecycle for the standalone SQLite catalog adapter."""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Literal

from mdrack_core.domain import (
    LexicalBranch,
    PreparedResourceBatch,
    RankedCandidate,
    ResourceRecord,
    SearchScope,
    SearchUnitRecord,
    VectorBranch,
    VectorRecord,
)
from mdrack_sqlite.contract import SQLITE_BRIDGE_SCHEMA_ID
from mdrack_sqlite.errors import SQLiteCatalogError, SQLiteErrorCode
from mdrack_sqlite.resource_store import SQLiteResourceStore

_REQUIRED_TABLES = frozenset(
    {
        "core_resources",
        "core_representations",
        "core_search_units",
        "core_embedding_spaces",
        "core_unit_embeddings",
        "core_facets",
        "core_resource_facets",
        "core_search_units_fts",
    }
)
_REQUIRED_INDEXES = frozenset(
    {
        "idx_core_resources_kind",
        "idx_core_resources_media",
        "idx_core_resources_namespace",
        "idx_core_resources_hash",
        "idx_core_representations_resource",
        "idx_core_representations_kind",
        "idx_core_representations_modality",
        "idx_core_units_resource",
        "idx_core_units_kind",
        "idx_core_units_modality",
        "idx_core_embeddings_space",
        "idx_core_spaces_metric",
        "idx_core_spaces_fingerprint",
        "idx_core_facets_lookup",
        "idx_core_resource_facets_facet",
        "idx_core_resource_facets_resource",
    }
)
_REQUIRED_FOREIGN_KEYS = frozenset(
    {
        ("core_representations", "resource_id", "core_resources", "resource_id", "CASCADE"),
        ("core_search_units", "resource_id", "core_representations", "resource_id", "CASCADE"),
        (
            "core_search_units",
            "representation_id",
            "core_representations",
            "representation_id",
            "CASCADE",
        ),
        ("core_unit_embeddings", "unit_id", "core_search_units", "unit_id", "CASCADE"),
        (
            "core_unit_embeddings",
            "space_id",
            "core_embedding_spaces",
            "space_id",
            "RESTRICT",
        ),
        ("core_resource_facets", "resource_id", "core_resources", "resource_id", "CASCADE"),
        ("core_resource_facets", "facet_id", "core_facets", "facet_id", "RESTRICT"),
    }
)


@dataclass(frozen=True)
class SQLiteVerification:
    """Privacy-safe verification summary for the frozen bridge contract."""

    schema_id: str
    resources: int
    representations: int
    units: int
    vectors: int
    facets: int
    fts_rows: int


class SQLiteCatalog(SQLiteResourceStore):
    """A single-connection catalog with explicit ownership and close semantics.

    Connections are thread-bound by sqlite3. A catalog serializes its own writes;
    concurrent readers use independently opened read-only catalogs. Caller-owned
    transactions are rejected by write operations inherited from
    :class:`SQLiteResourceStore`.
    """

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        readonly: bool = False,
        owns_connection: bool = False,
    ) -> None:
        super().__init__(connection)
        self._readonly = readonly
        self._owns_connection = owns_connection
        self._closed = False

    @classmethod
    def open(
        cls,
        database_path: str | Path,
        *,
        timeout: float = 5.0,
    ) -> SQLiteCatalog:
        """Open an existing bridge catalog for reads and writes.

        This Stage-3A API never creates or migrates a database. Clean standalone
        schema creation is owned by the subsequent migration slice.
        """
        return cls._open(database_path, timeout=timeout, readonly=False)

    @classmethod
    def open_readonly(
        cls,
        database_path: str | Path,
        *,
        timeout: float = 5.0,
    ) -> SQLiteCatalog:
        """Open an existing bridge catalog in SQLite read-only/query-only mode."""
        return cls._open(database_path, timeout=timeout, readonly=True)

    @classmethod
    def _open(
        cls,
        database_path: str | Path,
        *,
        timeout: float,
        readonly: bool,
    ) -> SQLiteCatalog:
        error_code = SQLiteErrorCode.READ_ONLY_OPEN_FAILED if readonly else SQLiteErrorCode.OPEN_FAILED
        try:
            if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
                raise ValueError
            timeout_value = float(timeout)
            if not math.isfinite(timeout_value) or timeout_value <= 0:
                raise ValueError
            path = Path(database_path).expanduser().resolve(strict=True)
            if not path.is_file():
                raise ValueError
            mode = "ro" if readonly else "rw"
            connection = sqlite3.connect(
                f"{path.as_uri()}?mode={mode}",
                uri=True,
                timeout=timeout_value,
            )
            try:
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute(f"PRAGMA busy_timeout={int(timeout_value * 1000)}")
                if readonly:
                    connection.execute("PRAGMA query_only=ON")
                else:
                    connection.execute("PRAGMA journal_mode=WAL")
                return cls(connection, readonly=readonly, owns_connection=True)
            except Exception:
                connection.close()
                raise
        except (TypeError, ValueError):
            raise SQLiteCatalogError(SQLiteErrorCode.INVALID_PATH) from None
        except Exception:
            raise SQLiteCatalogError(error_code) from None

    @property
    def readonly(self) -> bool:
        return self._readonly

    @property
    def closed(self) -> bool:
        return self._closed

    def replace_resource(self, batch: PreparedResourceBatch) -> None:
        self._require_open()
        super().replace_resource(batch)

    def delete_resource(self, resource_id: str) -> None:
        self._require_open()
        super().delete_resource(resource_id)

    def read_resource(self, resource_id: str) -> ResourceRecord | None:
        self._require_open()
        return super().read_resource(resource_id)

    def read_unit(self, unit_id: str) -> SearchUnitRecord | None:
        self._require_open()
        return super().read_unit(unit_id)

    def read_vector(self, unit_id: str, space_id: str) -> VectorRecord | None:
        self._require_open()
        return super().read_vector(unit_id, space_id)

    def find_by_content_hash(
        self,
        content_hash: str,
        *,
        scope: SearchScope,
    ) -> list[ResourceRecord]:
        self._require_open()
        return super().find_by_content_hash(content_hash, scope=scope)

    def search_lexical(
        self,
        branch: LexicalBranch,
        *,
        scope: SearchScope,
    ) -> list[RankedCandidate]:
        self._require_open()
        return super().search_lexical(branch, scope=scope)

    def search_vector(
        self,
        branch: VectorBranch,
        *,
        scope: SearchScope,
    ) -> list[RankedCandidate]:
        self._require_open()
        return super().search_vector(branch, scope=scope)

    def verify(self) -> SQLiteVerification:
        """Fail closed on transaction, integrity, schema, FK, index, or FTS drift."""
        self._require_open()
        if self.connection.in_transaction:
            raise SQLiteCatalogError(SQLiteErrorCode.ACTIVE_TRANSACTION)
        try:
            integrity = [row[0] for row in self.connection.execute("PRAGMA integrity_check")]
            if integrity != ["ok"]:
                raise ValueError
            if self.connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raise ValueError
            if self.connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise ValueError

            objects = {
                row[0]
                for row in self.connection.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
                )
            }
            indexes = {
                row[0]
                for row in self.connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='index'"
                )
            }
            if not _REQUIRED_TABLES <= objects or not _REQUIRED_INDEXES <= indexes:
                raise ValueError

            actual_foreign_keys: set[tuple[str, str, str, str, str]] = set()
            for table in (
                "core_representations",
                "core_search_units",
                "core_unit_embeddings",
                "core_resource_facets",
            ):
                for row in self.connection.execute(f"PRAGMA foreign_key_list({table})"):
                    actual_foreign_keys.add((table, row[3], row[2], row[4], row[6]))
            if not _REQUIRED_FOREIGN_KEYS <= actual_foreign_keys:
                raise ValueError

            expected_fts = self.connection.execute(
                "SELECT COUNT(*) FROM core_search_units "
                "WHERE text_content IS NOT NULL AND trim(text_content)<>''"
            ).fetchone()[0]
            actual_fts = self.connection.execute(
                "SELECT COUNT(*) FROM core_search_units_fts"
            ).fetchone()[0]
            distinct_fts = self.connection.execute(
                "SELECT COUNT(DISTINCT unit_id) FROM core_search_units_fts"
            ).fetchone()[0]
            if expected_fts != actual_fts or actual_fts != distinct_fts:
                raise ValueError

            return SQLiteVerification(
                schema_id=SQLITE_BRIDGE_SCHEMA_ID,
                resources=self._count("core_resources"),
                representations=self._count("core_representations"),
                units=self._count("core_search_units"),
                vectors=self._count("core_unit_embeddings"),
                facets=self._count("core_resource_facets"),
                fts_rows=actual_fts,
            )
        except SQLiteCatalogError:
            raise
        except Exception:
            raise SQLiteCatalogError(SQLiteErrorCode.VERIFY_FAILED) from None

    def close(self) -> None:
        """Roll back an unfinished transaction and close once; repeated calls are safe."""
        if self._closed:
            return
        try:
            if self.connection.in_transaction:
                self.connection.rollback()
            if self._owns_connection:
                self.connection.close()
        finally:
            self._closed = True

    def __enter__(self) -> SQLiteCatalog:
        self._require_open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        self.close()
        return False

    def _require_open(self) -> None:
        if self._closed:
            raise SQLiteCatalogError(SQLiteErrorCode.CLOSED)

    def _count(self, table: str) -> int:
        return int(self.connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
