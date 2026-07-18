"""Lifecycle, bridge parity, and schema verification for ``mdrack_sqlite``."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from mdrack.adapters.sqlite.resource_store import SQLiteResourceStore as LegacySQLiteResourceStore
from mdrack.storage.sqlite.connection import get_connection
from mdrack.storage.sqlite.migrations import apply_candidate_migrations, get_migrations_dir
from mdrack_core.domain import (
    CatalogExecutionError,
    EmbeddingSpaceRecord,
    Facet,
    LexicalBranch,
    Locator,
    PreparedResourceBatch,
    RepresentationRecord,
    ResourceFacet,
    ResourceRecord,
    SearchScope,
    SearchUnitRecord,
    VectorBranch,
    VectorRecord,
)
from mdrack_sqlite import (
    SQLITE_BRIDGE_SCHEMA_ID,
    SQLiteCatalog,
    SQLiteCatalogError,
    SQLiteErrorCode,
    SQLiteResourceStore,
)


def _create_bridge(path: Path) -> None:
    connection = get_connection(path)
    try:
        apply_candidate_migrations(connection, get_migrations_dir())
    finally:
        connection.close()


def _batch() -> PreparedResourceBatch:
    resource_id = "resource"
    representation_id = "representation"
    unit_id = "unit"
    return PreparedResourceBatch(
        ResourceRecord(
            resource_id,
            "document",
            "text/plain",
            "vault",
            Locator("logical", {"id": resource_id}),
            "sha256:content",
        ),
        [
            RepresentationRecord(
                representation_id,
                resource_id,
                "retrieval_text",
                "text",
                "needle text",
            )
        ],
        [
            SearchUnitRecord(
                unit_id,
                resource_id,
                representation_id,
                "text_chunk",
                "text",
                "needle text",
                Locator("whole", {}),
                0,
            )
        ],
        [EmbeddingSpaceRecord("space", 2, "dot", "fingerprint")],
        [VectorRecord(unit_id, "space", (1.0, 0.0))],
        [ResourceFacet(resource_id, Facet("tag", "included"), "user")],
    )


class _ForbiddenLock:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def __enter__(self) -> None:
        self._events.append("lock")
        raise AssertionError("closed catalog acquired the writer lock")

    def __exit__(self, *args: object) -> None:
        return None


def _graph_snapshot(database: Path) -> tuple[tuple[str, tuple[tuple[object, ...], ...]], ...]:
    tables = (
        "core_resources",
        "core_representations",
        "core_search_units",
        "core_embedding_spaces",
        "core_unit_embeddings",
        "core_facets",
        "core_resource_facets",
    )
    connection = sqlite3.connect(database)
    try:
        rows = [
            (table, tuple(tuple(row) for row in connection.execute(f'SELECT * FROM "{table}"')))
            for table in tables
        ]
        rows.append(
            (
                "core_search_units_fts",
                tuple(
                    tuple(row)
                    for row in connection.execute(
                        "SELECT rowid,unit_id,content FROM core_search_units_fts ORDER BY rowid"
                    )
                ),
            )
        )
        return tuple(rows)
    finally:
        connection.close()


def _call_catalog_operation(catalog: SQLiteCatalog, operation: str) -> None:
    if operation == "replace_resource":
        catalog.replace_resource(_batch())
    elif operation == "delete_resource":
        catalog.delete_resource("resource")
    elif operation == "read_resource":
        catalog.read_resource("resource")
    elif operation == "read_unit":
        catalog.read_unit("unit")
    elif operation == "read_vector":
        catalog.read_vector("unit", "space")
    elif operation == "find_by_content_hash":
        catalog.find_by_content_hash("sha256:content", scope=SearchScope())
    elif operation == "search_lexical":
        catalog.search_lexical(
            LexicalBranch("lexical", "needle", candidate_limit=1),
            scope=SearchScope(),
        )
    elif operation == "search_vector":
        catalog.search_vector(
            VectorBranch(
                "vector",
                "space",
                (1.0, 0.0),
                candidate_limit=1,
                expected_fingerprint="fingerprint",
            ),
            scope=SearchScope(),
        )
    else:  # pragma: no cover - guarded by the parameter matrix
        raise AssertionError(f"unknown operation: {operation}")


def test_legacy_import_is_exact_reexport_of_single_adapter_owner() -> None:
    assert LegacySQLiteResourceStore is SQLiteResourceStore
    assert SQLiteCatalog.__mro__[1] is SQLiteResourceStore


def test_context_open_verify_catalog_ports_and_close(tmp_path: Path) -> None:
    database = tmp_path / "bridge.db"
    _create_bridge(database)

    with SQLiteCatalog.open(database) as catalog:
        assert catalog.readonly is False
        assert catalog.closed is False
        empty = catalog.verify()
        assert empty.schema_id == SQLITE_BRIDGE_SCHEMA_ID
        assert (
            empty.resources,
            empty.representations,
            empty.units,
            empty.vectors,
            empty.facets,
            empty.fts_rows,
        ) == (0, 0, 0, 0, 0, 0)

        batch = _batch()
        catalog.replace_resource(batch)
        assert catalog.read_resource("resource") == batch.resource
        assert catalog.read_unit("unit") == batch.units[0]
        assert catalog.read_vector("unit", "space") == batch.vectors[0]
        assert catalog.find_by_content_hash(
            "sha256:content",
            scope=SearchScope(facets_all=[Facet("tag", "included")]),
        ) == [batch.resource]
        assert [
            item.unit_id
            for item in catalog.search_lexical(
                LexicalBranch("lexical", "needle", candidate_limit=1),
                scope=SearchScope(source_namespaces=["vault"]),
            )
        ] == ["unit"]
        assert [
            item.unit_id
            for item in catalog.search_vector(
                VectorBranch(
                    "vector",
                    "space",
                    (1.0, 0.0),
                    candidate_limit=1,
                    expected_fingerprint="fingerprint",
                ),
                scope=SearchScope(facets_none=[Facet("tag", "excluded")]),
            )
        ] == ["unit"]
        populated = catalog.verify()
        assert (
            populated.resources,
            populated.representations,
            populated.units,
            populated.vectors,
            populated.facets,
            populated.fts_rows,
        ) == (1, 1, 1, 1, 1, 1)

    assert catalog.closed is True
    catalog.close()
    with pytest.raises(SQLiteCatalogError) as closed:
        catalog.verify()
    assert closed.value.code is SQLiteErrorCode.CLOSED


@pytest.mark.parametrize("caller_owned", [False, True], ids=["owned", "caller-owned"])
@pytest.mark.parametrize(
    "operation",
    [
        "replace_resource",
        "delete_resource",
        "read_resource",
        "read_unit",
        "read_vector",
        "find_by_content_hash",
        "search_lexical",
        "search_vector",
    ],
)
def test_closed_catalog_rejects_every_catalog_operation_before_adapter_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caller_owned: bool,
    operation: str,
) -> None:
    database = tmp_path / f"closed-{operation}-{caller_owned}.db"
    _create_bridge(database)
    with SQLiteCatalog.open(database) as seed:
        seed.replace_resource(_batch())
    before = _graph_snapshot(database)

    connection = get_connection(database) if caller_owned else None
    catalog = (
        SQLiteCatalog(connection, owns_connection=False)
        if connection is not None
        else SQLiteCatalog.open(database)
    )
    failure_points: list[str] = []
    lock_events: list[str] = []
    catalog.set_failure_hook(failure_points.append)
    monkeypatch.setattr(catalog, "_writer_lock", _ForbiddenLock(lock_events))
    catalog.close()

    with pytest.raises(SQLiteCatalogError) as error:
        _call_catalog_operation(catalog, operation)

    assert error.value.code is SQLiteErrorCode.CLOSED
    assert catalog.transaction_open_count == 0
    assert failure_points == []
    assert lock_events == []
    assert _graph_snapshot(database) == before
    if connection is not None:
        assert connection.execute("SELECT COUNT(*) FROM core_resources").fetchone()[0] == 1
        connection.close()


def test_readonly_open_preserves_database_and_maps_writes_safely(tmp_path: Path) -> None:
    database = tmp_path / "readonly.db"
    _create_bridge(database)
    with SQLiteCatalog.open(database) as writable:
        writable.replace_resource(_batch())
    before = hashlib.sha256(database.read_bytes()).hexdigest()

    with SQLiteCatalog.open_readonly(database) as catalog:
        assert catalog.readonly is True
        assert catalog.read_resource("resource") == _batch().resource
        assert catalog.verify().resources == 1
        with pytest.raises(CatalogExecutionError) as error:
            catalog.delete_resource("resource")
        assert str(error.value) == "catalog_error"

    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    with SQLiteCatalog.open_readonly(database) as reopened:
        assert reopened.read_resource("resource") == _batch().resource


def test_verify_rejects_active_transaction_and_rolls_it_back_on_close(tmp_path: Path) -> None:
    database = tmp_path / "transaction.db"
    _create_bridge(database)
    catalog = SQLiteCatalog.open(database)
    catalog.connection.execute("BEGIN")
    with pytest.raises(SQLiteCatalogError) as error:
        catalog.verify()
    assert error.value.code is SQLiteErrorCode.ACTIVE_TRANSACTION
    catalog.close()

    with SQLiteCatalog.open_readonly(database) as reopened:
        assert reopened.verify().resources == 0


def test_verify_fails_closed_on_required_index_drift(tmp_path: Path) -> None:
    database = tmp_path / "drift.db"
    _create_bridge(database)
    connection = sqlite3.connect(database)
    try:
        connection.execute("DROP INDEX idx_core_resources_hash")
        connection.commit()
    finally:
        connection.close()

    with SQLiteCatalog.open(database) as catalog:
        with pytest.raises(SQLiteCatalogError) as error:
            catalog.verify()
        assert error.value.code is SQLiteErrorCode.VERIFY_FAILED
        assert "drift.db" not in str(error.value)


def test_open_failures_are_safe_and_do_not_create_database(tmp_path: Path) -> None:
    missing = tmp_path / "PRIVATE_PATH_SENTINEL.db"
    with pytest.raises(SQLiteCatalogError) as writable:
        SQLiteCatalog.open(missing)
    with pytest.raises(SQLiteCatalogError) as readonly:
        SQLiteCatalog.open_readonly(missing)

    assert writable.value.code is SQLiteErrorCode.OPEN_FAILED
    assert readonly.value.code is SQLiteErrorCode.READ_ONLY_OPEN_FAILED
    assert "PRIVATE_PATH_SENTINEL" not in str(writable.value)
    assert "PRIVATE_PATH_SENTINEL" not in str(readonly.value)
    assert not missing.exists()
