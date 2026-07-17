"""Contract tests for the v0.3 resource-core SQLite schema and adapter."""

from __future__ import annotations

import hashlib
import math
import shutil
import sqlite3
import struct
from collections.abc import Iterator
from pathlib import Path

import pytest

from mdrack.adapters.sqlite.resource_store import SQLiteResourceStore
from mdrack.storage.sqlite.connection import get_connection
from mdrack.storage.sqlite.migrations import (
    EXPECTED_MIGRATION_MANIFEST,
    EXPECTED_MIGRATION_VERSION,
    apply_candidate_migrations,
    get_migrations_dir,
)
from mdrack_core.application.indexing import CoreIndexingService
from mdrack_core.domain import (
    BranchExecutionError,
    CatalogExecutionError,
    EmbeddingSpaceRecord,
    ErrorCategory,
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

apply_migrations = apply_candidate_migrations
MIGRATIONS_DIR = get_migrations_dir()
LEGACY_MIGRATIONS = tuple(name for name, _digest in EXPECTED_MIGRATION_MANIFEST if not name.startswith("0007_"))
LEGACY_DATA_OBJECTS = (
    "files",
    "sections",
    "chunks",
    "chunks_fts",
    "embedding_profiles",
    "chunk_embeddings",
    "index_runs",
    "diagnostics",
    "assets",
    "asset_references",
    "asset_descriptions",
)
CORE_TABLES = {
    "core_resources",
    "core_representations",
    "core_search_units",
    "core_embedding_spaces",
    "core_unit_embeddings",
    "core_facets",
    "core_resource_facets",
    "core_search_units_fts",
}


def _apply_prefix(connection: sqlite3.Connection, names: tuple[str, ...]) -> None:
    for name in names:
        connection.executescript((MIGRATIONS_DIR / name).read_text(encoding="utf-8"))
        connection.execute("INSERT INTO schema_migrations(version) VALUES (?)", (name[:4],))
    connection.commit()


def _schema_identity(connection: sqlite3.Connection, *, legacy_only: bool) -> list[tuple[object, ...]]:
    rows = connection.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
    ).fetchall()
    values = [tuple(row) for row in rows]
    if legacy_only:
        values = [
            row
            for row in values
            if not str(row[1]).startswith("core_") and not str(row[2]).startswith("core_")
        ]
    return values


def _table_digest(connection: sqlite3.Connection, table: str) -> tuple[int, str]:
    columns = [row["name"] for row in connection.execute(f"PRAGMA table_info({table})")]
    order = ", ".join(f'"{name}"' for name in columns)
    rows = connection.execute(f'SELECT {order} FROM "{table}" ORDER BY {order}').fetchall()
    digest = hashlib.sha256()
    for row in rows:
        digest.update(repr(tuple(row)).encode("utf-8"))
        digest.update(b"\n")
    return len(rows), digest.hexdigest()


def _populate_legacy(connection: sqlite3.Connection) -> None:
    connection.execute(
        "INSERT INTO files(id, relative_path, title, source_hash, indexed_at) VALUES(?,?,?,?,?)",
        ("file", "legacy.md", "Legacy", "hash", "2026-01-01"),
    )
    connection.execute(
        "INSERT INTO sections(id,file_id,title,level,start_line,end_line) VALUES(?,?,?,?,?,?)",
        ("section", "file", "Legacy", 1, 1, 1),
    )
    connection.execute(
        "INSERT INTO chunks(id,file_id,section_id,content,content_type,chunk_index) VALUES(?,?,?,?,?,?)",
        ("chunk", "file", "section", "legacy sentinel", "text", 0),
    )
    connection.execute(
        "INSERT INTO chunks_fts(chunk_id,content,content_type,heading_path) VALUES(?,?,?,?)",
        ("chunk", "legacy sentinel", "text", "Legacy"),
    )
    connection.execute(
        "INSERT INTO embedding_profiles(name,model,dimensions,endpoint,fingerprint) VALUES(?,?,?,?,?)",
        ("profile", "model", 2, "local", "fp"),
    )
    connection.execute(
        "INSERT INTO chunk_embeddings(chunk_id,profile_name,embedding,embedded_at,profile_fingerprint) "
        "VALUES(?,?,?,?,?)",
        ("chunk", "profile", b"[0.0,-0.0]", "2026-01-01", "fp"),
    )
    connection.execute(
        "INSERT INTO index_runs(id,started_at,status,parser_name,parser_version,"
        "chunk_strategy_name,chunk_strategy_version) "
        "VALUES(?,?,?,?,?,?,?)",
        ("run", "2026-01-01", "completed", "parser", "1", "chunker", "1"),
    )
    connection.execute(
        "INSERT INTO diagnostics(id,run_id,severity,code,message,details,created_at) VALUES(?,?,?,?,?,?,?)",
        ("diag", "run", "info", "legacy", "safe", "{}", "2026-01-01"),
    )
    connection.execute(
        "INSERT INTO assets(asset_id,root_id,relative_path,exists_on_disk) VALUES(?,?,?,?)",
        ("asset", "root", "image.png", 1),
    )
    connection.execute(
        "INSERT INTO asset_references(reference_id,asset_id,file_id,document_logical_id,document_relative_path,"
        "block_logical_id,raw_reference,syntax,start_line,end_line,resolution_status) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        ("ref", "asset", "file", "doc", "legacy.md", "block", "![]()", "markdown", 1, 1, "resolved"),
    )
    connection.execute(
        "INSERT INTO asset_descriptions(asset_id,description_kind,description_text) VALUES(?,?,?)",
        ("asset", "caption", "legacy caption"),
    )
    connection.commit()


def _batch(
    resource_id: str = "resource-1",
    *,
    namespace: str = "vault",
    text: str | None = "alpha searchable text",
    vector: tuple[float, ...] = (0.0, -0.0),
    metric: str = "dot",
    fingerprint: str = "space-fingerprint",
    kind: str = "document",
    media_type: str = "text/markdown",
    representation_kind: str = "retrieval_text",
    modality: str = "text",
    unit_kind: str = "text_chunk",
    content_hash: str = "sha256:shared",
    facets: tuple[Facet, ...] = (Facet("tag", "python"),),
    space_id: str = "space",
) -> PreparedResourceBatch:
    representation_id = f"representation-{resource_id}"
    unit_id = f"unit-{resource_id}"
    resource = ResourceRecord(
        resource_id,
        kind,
        media_type,
        namespace,
        Locator("relative", {"path": f"{resource_id}.md", "unicode": "Привет"}),
        content_hash,
        "",
        {"nested": {"order": [2, 1]}},
    )
    representation = RepresentationRecord(
        representation_id,
        resource_id,
        representation_kind,
        modality,
        text,
        "ru",
        "producer",
        3,
        "exact",
        {"representation": True},
    )
    unit = SearchUnitRecord(
        unit_id,
        resource_id,
        representation_id,
        unit_kind,
        modality,
        text,
        Locator("span", {"start": 0, "end": 5}),
        0,
        3,
        "estimated",
        {"unit": True},
    )
    space = EmbeddingSpaceRecord(space_id, len(vector), metric, fingerprint, {"model": "fake"})
    assignments = tuple(
        ResourceFacet(resource_id, facet, "user", None, -0.0) for facet in facets
    )
    return PreparedResourceBatch(
        resource,
        [representation],
        [unit],
        [space],
        [VectorRecord(unit_id, space_id, vector)],
        assignments,
    )


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = get_connection(tmp_path / "resource.db")
    apply_migrations(connection, MIGRATIONS_DIR)
    yield connection
    connection.close()


@pytest.fixture
def store(connection: sqlite3.Connection) -> SQLiteResourceStore:
    return SQLiteResourceStore(connection)


def test_0007_is_create_only_and_preserves_populated_legacy_identity(tmp_path: Path) -> None:
    connection = get_connection(tmp_path / "populated.db")
    try:
        _apply_prefix(connection, LEGACY_MIGRATIONS)
        _populate_legacy(connection)
        schema_before = _schema_identity(connection, legacy_only=True)
        data_before = {table: _table_digest(connection, table) for table in LEGACY_DATA_OBJECTS}
        ledger_before = [tuple(row) for row in connection.execute("SELECT * FROM schema_migrations ORDER BY version")]

        apply_migrations(connection, MIGRATIONS_DIR)

        assert EXPECTED_MIGRATION_VERSION == "0007"
        assert _schema_identity(connection, legacy_only=True) == schema_before
        assert {table: _table_digest(connection, table) for table in LEGACY_DATA_OBJECTS} == data_before
        assert [tuple(row) for row in connection.execute(
            "SELECT * FROM schema_migrations WHERE version < '0007' ORDER BY version"
        )] == ledger_before
        assert connection.execute("SELECT COUNT(*) FROM schema_migrations WHERE version='0007'").fetchone()[0] == 1
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
        assert CORE_TABLES <= tables
        for table in CORE_TABLES - {"core_search_units_fts"}:
            assert connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] == 0
    finally:
        connection.close()


def test_0007_failure_is_atomic_for_schema_and_ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    copied = tmp_path / "migrations"
    shutil.copytree(MIGRATIONS_DIR, copied)
    broken = copied / "0007_resource_core.sql"
    broken.write_text(broken.read_text(encoding="utf-8") + "\nTHIS IS NOT SQL;\n", encoding="utf-8")
    manifest = tuple(
        (path.name, hashlib.sha256(path.read_bytes()).hexdigest()) for path in sorted(copied.glob("*.sql"))
    )
    monkeypatch.setattr("mdrack.storage.sqlite.migrations.EXPECTED_MIGRATION_MANIFEST", manifest)
    from mdrack.storage.sqlite.migrations import _framed_manifest_digest

    monkeypatch.setattr(
        "mdrack.storage.sqlite.migrations.EXPECTED_MIGRATION_MANIFEST_DIGEST",
        _framed_manifest_digest([(path.name, path.read_bytes()) for path in sorted(copied.glob("*.sql"))]),
    )
    connection = get_connection(tmp_path / "failed.db")
    try:
        _apply_prefix(connection, LEGACY_MIGRATIONS)
        with pytest.raises(sqlite3.Error):
            apply_migrations(connection, copied)
        assert connection.execute("SELECT COUNT(*) FROM schema_migrations WHERE version='0007'").fetchone()[0] == 0
        names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
        assert not CORE_TABLES.intersection(names)
    finally:
        connection.close()


def test_catalog_round_trip_signed_zero_facets_hash_scope_and_delete(store: SQLiteResourceStore) -> None:
    first = _batch()
    second = _batch("resource-2", namespace="other")
    store.replace_resource(first)
    store.replace_resource(second)

    assert store.read_resource("resource-1") == first.resource
    assert store.read_unit("unit-resource-1") == first.units[0]
    actual_vector = store.read_vector("unit-resource-1", "space")
    assert actual_vector is not None
    assert struct.pack(">2d", *actual_vector.vector) == struct.pack(">2d", 0.0, -0.0)
    assert math.copysign(1.0, actual_vector.vector[1]) == -1.0
    matches = store.find_by_content_hash(
        "sha256:shared", scope=SearchScope(source_namespaces=["vault"])
    )
    assert matches == [first.resource]

    store.delete_resource("resource-1")
    store.delete_resource("resource-1")
    assert store.read_resource("resource-1") is None
    assert store.read_unit("unit-resource-1") is None
    assert store.read_vector("unit-resource-1", "space") is None


def test_replace_is_atomic_rejects_caller_transaction_and_preserves_old_graph(
    connection: sqlite3.Connection,
) -> None:
    store = SQLiteResourceStore(connection)
    original = _batch()
    store.replace_resource(original)

    connection.execute("BEGIN")
    with pytest.raises(CatalogExecutionError) as active:
        store.replace_resource(_batch(text="changed"))
    assert active.value.category is ErrorCategory.CATALOG_ERROR
    connection.rollback()
    assert store.read_unit("unit-resource-1") == original.units[0]

    replacement = _batch(text="changed")
    store.set_failure_hook(
        lambda point: (
            (_ for _ in ()).throw(RuntimeError("PRIVATE_SQL_SENTINEL"))
            if point == "after_units"
            else None
        )
    )
    with pytest.raises(CatalogExecutionError) as injected:
        store.replace_resource(replacement)
    assert str(injected.value) == "catalog_error"
    assert store.read_unit("unit-resource-1") == original.units[0]


@pytest.mark.parametrize(
    "failure_point",
    [
        "before_begin",
        "after_begin",
        "after_delete",
        "after_representations",
        "after_units",
        "after_vectors",
        "after_facets",
        "after_fts",
        "before_commit",
    ],
)
def test_every_replace_failure_point_preserves_the_old_complete_graph(
    store: SQLiteResourceStore,
    failure_point: str,
) -> None:
    original = _batch()
    store.replace_resource(original)

    def fail(point: str) -> None:
        if point == failure_point:
            raise RuntimeError("PRIVATE_FAILURE_SENTINEL")

    store.set_failure_hook(fail)
    with pytest.raises(CatalogExecutionError) as error:
        store.replace_resource(_batch(text="replacement"))
    assert str(error.value) == "catalog_error"
    store.set_failure_hook(None)
    assert store.read_resource("resource-1") == original.resource
    assert store.read_unit("unit-resource-1") == original.units[0]
    assert store.read_vector("unit-resource-1", "space") == original.vectors[0]


@pytest.mark.parametrize(
    "failure_point",
    ["before_begin", "after_begin", "after_delete", "before_commit"],
)
def test_every_delete_failure_point_preserves_the_old_complete_graph(
    store: SQLiteResourceStore,
    failure_point: str,
) -> None:
    original = _batch()
    store.replace_resource(original)

    def fail(point: str) -> None:
        if point == failure_point:
            raise RuntimeError("PRIVATE_DELETE_FAILURE_SENTINEL")

    store.set_failure_hook(fail)
    with pytest.raises(CatalogExecutionError) as error:
        store.delete_resource("resource-1")
    assert str(error.value) == "catalog_error"
    store.set_failure_hook(None)
    assert store.read_resource("resource-1") == original.resource
    assert store.read_unit("unit-resource-1") == original.units[0]
    assert store.read_vector("unit-resource-1", "space") == original.vectors[0]


def test_typed_null_facets_signed_zero_orphans_and_corruption_fail_closed(
    store: SQLiteResourceStore,
    connection: sqlite3.Connection,
) -> None:
    original = _batch()
    lookalike = ResourceFacet(
        "resource-1",
        Facet("tag", "python"),
        "user",
        "__null__",
        -0.0,
    )
    batch = PreparedResourceBatch(
        original.resource,
        original.representations,
        original.units,
        original.spaces,
        original.vectors,
        (*original.facets, lookalike),
    )
    store.replace_resource(batch)
    rows = connection.execute(
        "SELECT producer_is_null,producer_value,confidence_json "
        "FROM core_resource_facets ORDER BY producer_is_null,producer_value"
    ).fetchall()
    assert [tuple(row) for row in rows] == [
        (0, "__null__", b"-0.0"),
        (1, "", b"-0.0"),
    ]

    connection.execute("PRAGMA ignore_check_constraints=ON")
    connection.execute(
        "UPDATE core_resource_facets SET producer_is_null=1,producer_value='corrupt' "
        "WHERE producer_is_null=0"
    )
    connection.commit()
    with pytest.raises(CatalogExecutionError):
        store.read_resource("resource-1")
    connection.execute("PRAGMA ignore_check_constraints=OFF")
    connection.execute(
        "UPDATE core_resource_facets SET producer_is_null=0,producer_value='__null__' "
        "WHERE producer_value='corrupt'"
    )
    connection.commit()
    store.delete_resource("resource-1")
    assert connection.execute("SELECT COUNT(*) FROM core_facets").fetchone()[0] == 0


def test_noncanonical_vector_bytes_fail_closed(
    store: SQLiteResourceStore,
    connection: sqlite3.Connection,
) -> None:
    store.replace_resource(_batch())
    connection.execute(
        "UPDATE core_unit_embeddings SET embedding=?",
        (b"[0.0, -0.0]",),
    )
    connection.commit()
    with pytest.raises(CatalogExecutionError):
        store.read_vector("unit-resource-1", "space")


def test_source_identity_space_compatibility_and_corruption_fail_closed(
    store: SQLiteResourceStore,
    connection: sqlite3.Connection,
) -> None:
    store.replace_resource(_batch())
    conflicting = _batch("other-id")
    object.__setattr__(conflicting.resource, "locator", _batch().resource.locator)
    with pytest.raises(CatalogExecutionError):
        store.replace_resource(conflicting)
    with pytest.raises(CatalogExecutionError):
        store.replace_resource(_batch(fingerprint="different"))

    connection.execute("UPDATE core_resources SET metadata_json='not canonical'")
    connection.commit()
    with pytest.raises(CatalogExecutionError) as error:
        store.read_resource("resource-1")
    assert str(error.value) == "catalog_error"


@pytest.mark.parametrize("corruption", ["modality", "representation_owner"])
def test_unit_owner_corruption_fails_catalog_reads_and_both_searches_closed(
    store: SQLiteResourceStore,
    connection: sqlite3.Connection,
    corruption: str,
) -> None:
    store.replace_resource(_batch())
    if corruption == "modality":
        connection.execute("UPDATE core_search_units SET modality='image'")
        connection.commit()
    else:
        store.replace_resource(_batch("resource-2"))
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute(
            "UPDATE core_search_units SET representation_id='representation-resource-2', ordinal=1 "
            "WHERE unit_id='unit-resource-1'"
        )
        connection.commit()
        connection.execute("PRAGMA foreign_keys=ON")

    with pytest.raises(CatalogExecutionError) as read_error:
        store.read_unit("unit-resource-1")
    assert str(read_error.value) == "catalog_error"
    with pytest.raises(CatalogExecutionError) as lexical_error:
        store.search_lexical(
            LexicalBranch("lexical-corruption", "alpha"),
            scope=SearchScope(),
        )
    assert str(lexical_error.value) == "catalog_error"
    with pytest.raises(CatalogExecutionError) as vector_error:
        store.search_vector(
            VectorBranch("vector-corruption", "space", (1.0, 0.0)),
            scope=SearchScope(),
        )
    assert str(vector_error.value) == "catalog_error"


def test_manual_fts_scope_before_limit_update_and_delete(store: SQLiteResourceStore) -> None:
    excluded = _batch("excluded", namespace="other", text="alpha alpha alpha alpha")
    included = _batch("included", namespace="vault", text="alpha")
    store.replace_resource(excluded)
    store.replace_resource(included)

    results = store.search_lexical(
        LexicalBranch("lexical", "alpha", candidate_limit=1),
        scope=SearchScope(source_namespaces=["vault"]),
    )
    assert [item.unit_id for item in results] == ["unit-included"]

    store.replace_resource(_batch("included", namespace="vault", text="beta"))
    assert store.search_lexical(
        LexicalBranch("lexical", "alpha", candidate_limit=10), scope=SearchScope()
    )[0].unit_id == "unit-excluded"
    store.delete_resource("excluded")
    assert store.search_lexical(
        LexicalBranch("lexical", "alpha", candidate_limit=10), scope=SearchScope()
    ) == []


def test_vector_metrics_scope_facets_and_cosine_zero_boundary(store: SQLiteResourceStore) -> None:
    store.replace_resource(_batch("excluded", namespace="other", vector=(100.0, 0.0)))
    store.replace_resource(_batch("included", namespace="vault", vector=(1.0, 0.0)))
    results = store.search_vector(
        VectorBranch("semantic", "space", (1.0, 0.0), candidate_limit=1),
        scope=SearchScope(source_namespaces=["vault"], facets_all=[Facet("tag", "python")]),
    )
    assert [item.unit_id for item in results] == ["unit-included"]

    cosine_connection = store.connection
    cosine_store = SQLiteResourceStore(cosine_connection)
    cosine_store.delete_resource("excluded")
    cosine_store.delete_resource("included")
    cosine_store.replace_resource(
        _batch(metric="cosine", vector=(0.0, -0.0), space_id="cosine-space")
    )
    with pytest.raises(BranchExecutionError) as error:
        cosine_store.search_vector(
            VectorBranch("cosine", "cosine-space", (1.0, 0.0)), scope=SearchScope()
        )
    assert error.value.category is ErrorCategory.INCOMPATIBLE_VECTOR_SPACE


@pytest.mark.parametrize(
    "scope",
    [
        SearchScope(resource_kinds=["image"]),
        SearchScope(media_types=["image/png"]),
        SearchScope(source_namespaces=["other"]),
        SearchScope(representation_kinds=["caption"]),
        SearchScope(modalities=["image"]),
        SearchScope(unit_kinds=["region"]),
        SearchScope(facets_any=[Facet("tag", "missing")]),
        SearchScope(facets_all=[Facet("tag", "python"), Facet("tag", "missing")]),
        SearchScope(facets_none=[Facet("tag", "python")]),
    ],
)
def test_every_scope_filter_excludes_before_search_limit(
    store: SQLiteResourceStore,
    scope: SearchScope,
) -> None:
    store.replace_resource(_batch())
    assert store.search_lexical(LexicalBranch("lexical", "alpha", candidate_limit=1), scope=scope) == []
    assert store.search_vector(VectorBranch("vector", "space", (1.0, 0.0), candidate_limit=1), scope=scope) == []


def test_adapter_maps_busy_and_raw_failures_to_privacy_safe_categories(
    tmp_path: Path,
) -> None:
    first = get_connection(tmp_path / "busy.db")
    apply_migrations(first, MIGRATIONS_DIR)
    second = get_connection(tmp_path / "busy.db")
    second.execute("PRAGMA busy_timeout=1")
    try:
        first.execute("BEGIN IMMEDIATE")
        with pytest.raises(CatalogExecutionError) as error:
            SQLiteResourceStore(second).replace_resource(_batch())
        assert error.value.category is ErrorCategory.ADAPTER_TIMEOUT
        assert "busy.db" not in str(error.value)
    finally:
        first.rollback()
        first.close()
        second.close()


def test_core_service_and_direct_adapter_preflight_fail_before_mutation(
    store: SQLiteResourceStore,
    connection: sqlite3.Connection,
) -> None:
    service = CoreIndexingService(store)
    invalid = _batch()
    object.__setattr__(invalid.resource, "title", "\ud800")
    with pytest.raises(Exception) as error:
        service.index(invalid)
    assert str(error.value) == "validation"
    assert connection.execute("SELECT COUNT(*) FROM core_resources").fetchone()[0] == 0

    with pytest.raises(CatalogExecutionError) as direct:
        store.replace_resource(invalid)
    assert str(direct.value) == "catalog_error"
    assert store.transaction_open_count == 0
    assert connection.execute("SELECT COUNT(*) FROM core_resources").fetchone()[0] == 0
