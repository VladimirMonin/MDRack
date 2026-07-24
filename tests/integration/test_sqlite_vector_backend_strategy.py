"""Contract tests for explicit SQLite vector-backend composition."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from mdrack_core.domain import (
    CatalogExecutionError,
    EmbeddingSpaceRecord,
    Locator,
    PreparedResourceBatch,
    RepresentationRecord,
    ResourceRecord,
    SearchScope,
    SearchUnitRecord,
    VectorBranch,
    VectorRecord,
)
from mdrack_sqlite import SQLiteCatalog, SQLiteCatalogError, SQLiteErrorCode
from mdrack_sqlite.builtin_exact import BuiltinExactVectorBackend
from mdrack_sqlite.vector_backends import (
    SQLiteVectorBackend,
    SQLiteVectorCapabilities,
    SQLiteVectorGenerationContext,
    SQLiteVectorSchemaExtension,
    SQLiteVectorSchemaObject,
)

_PLUGIN_OBJECT = SQLiteVectorSchemaObject(
    "table",
    "plugin_vectors",
    "plugin_vectors",
    "CREATE TABLE plugin_vectors (resource_id TEXT PRIMARY KEY)",
)
_PLUGIN_EXTENSION = SQLiteVectorSchemaExtension("plugin_", (_PLUGIN_OBJECT,))


def test_builtin_backend_remains_the_extensionless_default(tmp_path: Path) -> None:
    database = tmp_path / "builtin-default.db"
    with SQLiteCatalog.create_v2(database) as catalog:
        assert isinstance(catalog.vector_backend, BuiltinExactVectorBackend)
        assert catalog.vector_backend.schema_extension() is None

    with SQLiteCatalog.open(database) as reopened:
        assert isinstance(reopened.vector_backend, BuiltinExactVectorBackend)
        assert reopened.verify().schema_id == "mdrack_sqlite_catalog_v2"


def _batch(resource_id: str = "resource") -> PreparedResourceBatch:
    representation_id = f"representation-{resource_id}"
    unit_id = f"unit-{resource_id}"
    return PreparedResourceBatch(
        ResourceRecord(
            resource_id,
            "document",
            "text/plain",
            "vault",
            Locator("logical", {"id": resource_id}),
            f"sha256:{resource_id}",
        ),
        (RepresentationRecord(representation_id, resource_id, "retrieval_text", "text", resource_id),),
        (
            SearchUnitRecord(
                unit_id,
                resource_id,
                representation_id,
                "text_chunk",
                "text",
                resource_id,
                Locator("whole", {}),
                0,
            ),
        ),
        (EmbeddingSpaceRecord("space", 2, "dot", "fingerprint"),),
        (VectorRecord(unit_id, "space", (1.0, 0.0)),),
        (),
    )


class _RecordingBackend:
    backend_id = "recording-v1"

    def __init__(
        self,
        *,
        fail_replace: bool = False,
        extension: SQLiteVectorSchemaExtension | None = _PLUGIN_EXTENSION,
    ) -> None:
        self.events: list[tuple[str, bool, bool]] = []
        self.fail_replace = fail_replace
        self._extension = extension
        self._fallback = BuiltinExactVectorBackend()

    def capabilities(self) -> SQLiteVectorCapabilities:
        return SQLiteVectorCapabilities(
            backend_id=self.backend_id,
            exact=True,
            metrics=frozenset({"cosine", "dot", "l2"}),
            supports_facets_any=True,
            supports_facets_all=True,
            supports_facets_none=True,
            supported_scope_fields=frozenset(
                {
                    "resource_kinds",
                    "media_types",
                    "source_namespaces",
                    "representation_kinds",
                    "modalities",
                    "unit_kinds",
                }
            ),
            supports_extensionless_open=False,
            supports_atomic_replace=True,
            supports_atomic_delete=True,
        )

    def schema_extension(self) -> SQLiteVectorSchemaExtension | None:
        return self._extension

    def initialize(self, connection: sqlite3.Connection, *, generation: SQLiteVectorGenerationContext) -> None:
        self.events.append(("initialize", connection.in_transaction, generation.create))
        if generation.create:
            connection.execute(_PLUGIN_OBJECT.sql)

    def delete_vectors(self, connection: sqlite3.Connection, *, resource_id: str) -> None:
        self.events.append(("delete", connection.in_transaction, False))
        connection.execute("DELETE FROM plugin_vectors WHERE resource_id=?", (resource_id,))

    def replace_vectors(
        self,
        connection: sqlite3.Connection,
        *,
        resource_id: str,
        spaces: Sequence[EmbeddingSpaceRecord],
        vectors: Sequence[VectorRecord],
    ) -> None:
        del spaces, vectors
        self.events.append(("replace", connection.in_transaction, False))
        connection.execute("INSERT INTO plugin_vectors(resource_id) VALUES(?)", (resource_id,))
        if self.fail_replace:
            raise RuntimeError("injected backend failure")

    def search(
        self,
        connection: sqlite3.Connection,
        *,
        branch: VectorBranch,
        dimensions: int,
        metric: str,
        metadata: Mapping[str, object],
        clauses: Sequence[str],
        params: Sequence[object],
    ) -> Any:
        self.events.append(("search", connection.in_transaction, False))
        return self._fallback.search(
            connection,
            branch=branch,
            dimensions=dimensions,
            metric=metric,
            metadata=metadata,
            clauses=clauses,
            params=params,
        )

    def verify(self, connection: sqlite3.Connection, *, generation: SQLiteVectorGenerationContext) -> None:
        self.events.append(("verify", connection.in_transaction, generation.create))
        assert connection.execute("SELECT COUNT(*) FROM plugin_vectors").fetchone() is not None


@pytest.fixture
def backend() -> _RecordingBackend:
    return _RecordingBackend()


def test_backend_protocol_and_catalog_injection_keep_transaction_ownership(
    tmp_path: Path,
    backend: _RecordingBackend,
) -> None:
    assert isinstance(backend, SQLiteVectorBackend)
    database = tmp_path / "plugin.db"
    with SQLiteCatalog.create_v2(database, vector_backend=backend) as catalog:
        assert backend.events == [("initialize", True, True), ("verify", False, False)]
        backend.events.clear()

        catalog.replace_resource(_batch())
        assert backend.events == [
            ("delete", True, False),
            ("replace", True, False),
            ("verify", True, False),
        ]
        assert [tuple(row) for row in catalog.connection.execute("SELECT resource_id FROM plugin_vectors")] == [
            ("resource",)
        ]

        backend.events.clear()
        assert [
            result.unit_id
            for result in catalog.search_vector(
                VectorBranch("branch", "space", (1.0, 0.0), expected_fingerprint="fingerprint"),
                scope=SearchScope(),
            )
        ] == ["unit-resource"]
        assert backend.events == [("search", False, False)]

        backend.events.clear()
        catalog.delete_resource("resource")
        assert backend.events == [("delete", True, False), ("verify", True, False)]
        assert catalog.connection.execute("SELECT * FROM plugin_vectors").fetchall() == []


def test_backend_failure_rolls_back_relational_and_plugin_rows(tmp_path: Path) -> None:
    backend = _RecordingBackend()
    with SQLiteCatalog.create_v2(tmp_path / "rollback.db", vector_backend=backend) as catalog:
        catalog.replace_resource(_batch("stable"))
        backend.events.clear()
        backend.fail_replace = True

        with pytest.raises(CatalogExecutionError):
            catalog.replace_resource(_batch("replacement"))

        assert catalog.read_resource("stable") == _batch("stable").resource
        assert catalog.read_resource("replacement") is None
        assert [tuple(row) for row in catalog.connection.execute("SELECT resource_id FROM plugin_vectors")] == [
            ("stable",)
        ]
        assert backend.events[:2] == [("delete", True, False), ("replace", True, False)]


def test_valid_registered_plugin_schema_opens_only_with_the_exact_extension(tmp_path: Path) -> None:
    database = tmp_path / "registered.db"
    with SQLiteCatalog.create_v2(database, vector_backend=_RecordingBackend()):
        pass

    with SQLiteCatalog.open(database, vector_backend=_RecordingBackend()) as reopened:
        assert reopened.verify().schema_id == "mdrack_sqlite_catalog_v2"

    with pytest.raises(SQLiteCatalogError) as extensionless:
        SQLiteCatalog.open(database)
    assert extensionless.value.code is SQLiteErrorCode.SCHEMA_MISMATCH

    tampered_extension = SQLiteVectorSchemaExtension(
        "plugin_",
        (
            SQLiteVectorSchemaObject(
                "table",
                "plugin_vectors",
                "plugin_vectors",
                "CREATE TABLE plugin_vectors (resource_id TEXT PRIMARY KEY, changed TEXT)",
            ),
        ),
    )
    with pytest.raises(SQLiteCatalogError) as tampered:
        SQLiteCatalog.open(database, vector_backend=_RecordingBackend(extension=tampered_extension))
    assert tampered.value.code is SQLiteErrorCode.SCHEMA_MISMATCH


def test_registered_schema_rejects_unknown_plugin_objects_and_base_drift(tmp_path: Path) -> None:
    unknown_database = tmp_path / "unknown-plugin.db"
    with SQLiteCatalog.create_v2(unknown_database, vector_backend=_RecordingBackend()):
        pass
    connection = sqlite3.connect(unknown_database)
    try:
        connection.execute("CREATE TABLE plugin_unknown (value TEXT)")
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(SQLiteCatalogError) as unknown:
        SQLiteCatalog.open(unknown_database, vector_backend=_RecordingBackend())
    assert unknown.value.code is SQLiteErrorCode.SCHEMA_MISMATCH

    base_database = tmp_path / "base-drift.db"
    with SQLiteCatalog.create_v2(base_database, vector_backend=_RecordingBackend()):
        pass
    connection = sqlite3.connect(base_database)
    try:
        connection.execute("CREATE TABLE base_schema_drift (value TEXT)")
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(SQLiteCatalogError) as base_drift:
        SQLiteCatalog.open(base_database, vector_backend=_RecordingBackend())
    assert base_drift.value.code is SQLiteErrorCode.SCHEMA_MISMATCH
