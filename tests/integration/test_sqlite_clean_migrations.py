"""Clean-package migration identity and lifecycle acceptance for ``mdrack_sqlite``."""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
from pathlib import Path

import pytest

from mdrack.storage.sqlite.connection import get_connection
from mdrack.storage.sqlite.migrations import (
    apply_candidate_migrations,
)
from mdrack.storage.sqlite.migrations import (
    get_migrations_dir as get_app_migrations_dir,
)
from mdrack_core.domain import (
    CatalogExecutionError,
    EmbeddingSpaceRecord,
    Locator,
    PreparedResourceBatch,
    RepresentationRecord,
    ResourceRecord,
    SearchUnitRecord,
    VectorRecord,
)
from mdrack_sqlite import (
    SQLITE_CATALOG_SCHEMA_ID,
    SQLITE_MIGRATION_MANIFEST,
    SQLITE_MIGRATION_MANIFEST_DIGEST,
    SQLiteCatalog,
    SQLiteCatalogError,
    SQLiteErrorCode,
)
from mdrack_sqlite import migrations as migration_module
from mdrack_sqlite import migrations_v2 as v2_migration_module
from mdrack_sqlite.contract_v2 import (
    SQLITE_CATALOG_V2_SCHEMA_ID,
    SQLITE_CATALOG_V2_SCHEMA_VERSION,
    SQLITE_V2_MIGRATION_MANIFEST,
    SQLITE_V2_MIGRATION_MANIFEST_DIGEST,
)
from mdrack_sqlite.migrations import (
    SQLiteMigrationError,
    apply_migrations,
    framed_manifest_digest,
    get_migrations_dir,
)
from mdrack_sqlite.migrations_v2 import (
    apply_v2_migrations,
    get_v2_migrations_dir,
)


def _batch(resource_id: str = "resource") -> PreparedResourceBatch:
    representation_id = f"representation-{resource_id}"
    unit_id = f"unit-{resource_id}"
    return PreparedResourceBatch(
        ResourceRecord(
            resource_id,
            "document",
            "text/plain",
            "fixture",
            Locator("logical", {"id": resource_id}),
            "sha256:content",
        ),
        [RepresentationRecord(representation_id, resource_id, "retrieval_text", "text", "needle")],
        [
            SearchUnitRecord(
                unit_id,
                resource_id,
                representation_id,
                "text_chunk",
                "text",
                "needle",
                Locator("whole", {}),
                0,
            )
        ],
        [EmbeddingSpaceRecord("space", 2, "dot", "fingerprint")],
        [VectorRecord(unit_id, "space", (1.0, 0.0))],
    )


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def test_compiled_clean_manifest_has_exact_independent_identity() -> None:
    migrations_dir = get_migrations_dir()
    entries = [(name, (migrations_dir / name).read_bytes()) for name, _digest in SQLITE_MIGRATION_MANIFEST]

    assert [name[:4] for name, _digest in SQLITE_MIGRATION_MANIFEST] == [
        "0000",
        "0001",
        "0002",
        "0003",
    ]
    assert all(hashlib.sha256(content).hexdigest() == digest for (name, content), (_, digest) in zip(
        entries, SQLITE_MIGRATION_MANIFEST, strict=True
    ))
    assert framed_manifest_digest(entries) == SQLITE_MIGRATION_MANIFEST_DIGEST
    assert SQLITE_CATALOG_SCHEMA_ID == "mdrack_sqlite_catalog_v1"


def test_v2_clean_catalog_has_independent_manifest_registry_and_reopen_contract(tmp_path: Path) -> None:
    database = tmp_path / "v2-clean.db"
    entries = [
        (name, (get_v2_migrations_dir() / name).read_bytes())
        for name, _digest in SQLITE_V2_MIGRATION_MANIFEST
    ]

    assert [name[:4] for name, _digest in SQLITE_V2_MIGRATION_MANIFEST] == [
        "0000",
        "0001",
        "0002",
        "0003",
        "0004",
    ]
    assert all(
        hashlib.sha256(content).hexdigest() == digest
        for (_name, content), (_manifest_name, digest) in zip(
            entries, SQLITE_V2_MIGRATION_MANIFEST, strict=True
        )
    )
    assert framed_manifest_digest(entries) == SQLITE_V2_MIGRATION_MANIFEST_DIGEST

    with SQLiteCatalog.create_v2(database) as catalog:
        assert catalog.schema_id == SQLITE_CATALOG_V2_SCHEMA_ID
        assert catalog.verify().schema_id == SQLITE_CATALOG_V2_SCHEMA_ID
        assert [
            tuple(row)
            for row in catalog.connection.execute(
                "SELECT codec_id,codec_version,component_type,byte_order,lossy "
                "FROM mdrack_vector_codecs ORDER BY codec_id"
            )
        ] == [
            ("ieee754-f32-le-v1", 1, "float32", "little", 0),
            ("ieee754-f64-le-v1", 1, "float64", "little", 0),
        ]
        assert [
            tuple(row)
            for row in catalog.connection.execute(
                "SELECT backend_id,backend_schema_version,extension_required,"
                "supports_atomic_replace,supports_atomic_delete "
                "FROM mdrack_vector_backends"
            )
        ] == [("builtin-exact-v1", 1, 0, 1, 1)]

    with SQLiteCatalog.open_readonly(database) as reopened:
        assert reopened.schema_id == SQLITE_CATALOG_V2_SCHEMA_ID
        assert tuple(
            reopened.connection.execute(
                "SELECT schema_version,manifest_digest FROM mdrack_sqlite_schema WHERE singleton=1"
            ).fetchone()
        ) == (SQLITE_CATALOG_V2_SCHEMA_VERSION, SQLITE_V2_MIGRATION_MANIFEST_DIGEST)


@pytest.mark.parametrize("failure_point", ["after_exclusive_create", "after_catalog_migrations"])
def test_v2_fresh_create_failure_removes_candidate_and_sqlite_sidecars(
    tmp_path: Path,
    failure_point: str,
) -> None:
    database = tmp_path / "failed-v2-create.db"

    def fail(point: str) -> None:
        if point == failure_point:
            raise RuntimeError("failure-injection")

    with pytest.raises(SQLiteCatalogError) as error:
        SQLiteCatalog.create_v2(database, failure_hook=fail)

    assert error.value.code is SQLiteErrorCode.OPEN_FAILED
    assert not database.exists()
    assert not database.with_name(database.name + "-wal").exists()
    assert not database.with_name(database.name + "-shm").exists()


def test_v2_migration_rejects_app_bridge_without_mutating_its_history(tmp_path: Path) -> None:
    database = tmp_path / "frozen-bridge.db"
    connection = get_connection(database)
    apply_candidate_migrations(connection, get_app_migrations_dir())
    versions = [
        row[0] for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version")
    ]

    with pytest.raises(SQLiteMigrationError):
        apply_v2_migrations(connection)

    assert [
        row[0] for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version")
    ] == versions
    assert connection.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE name='mdrack_vector_codecs'"
    ).fetchone()[0] == 0
    connection.close()


def test_failed_v2_migration_is_atomic_and_exact_prefix_can_resume(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    copied = tmp_path / "v2-migrations"
    shutil.copytree(get_v2_migrations_dir(), copied)
    broken = copied / "0004_vector_encoding.sql"
    original = broken.read_bytes()
    broken.write_bytes(original + b"\nTHIS IS NOT SQL;\n")

    def install_manifest() -> None:
        entries = [(path.name, path.read_bytes()) for path in sorted(copied.glob("*.sql"))]
        monkeypatch.setattr(
            v2_migration_module,
            "SQLITE_V2_MIGRATION_MANIFEST",
            tuple((name, hashlib.sha256(content).hexdigest()) for name, content in entries),
        )
        monkeypatch.setattr(
            v2_migration_module,
            "SQLITE_V2_MIGRATION_MANIFEST_DIGEST",
            framed_manifest_digest(entries),
        )

    install_manifest()
    database = tmp_path / "v2-interrupted.db"
    connection = _connect(database)
    with pytest.raises(SQLiteMigrationError):
        apply_v2_migrations(connection, copied)
    assert [
        row[0]
        for row in connection.execute(
            "SELECT version FROM mdrack_sqlite_migrations ORDER BY version"
        )
    ] == ["0000", "0001", "0002", "0003"]
    assert connection.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE name='mdrack_vector_codecs'"
    ).fetchone()[0] == 0

    broken.write_bytes(original)
    install_manifest()
    apply_v2_migrations(connection, copied)
    assert tuple(
        connection.execute(
            "SELECT schema_id,schema_version,manifest_digest FROM mdrack_sqlite_schema WHERE singleton=1"
        ).fetchone()
    ) == (
        SQLITE_CATALOG_V2_SCHEMA_ID,
        SQLITE_CATALOG_V2_SCHEMA_VERSION,
        SQLITE_V2_MIGRATION_MANIFEST_DIGEST,
    )
    connection.close()


def test_clean_core_schema_matches_frozen_app_bridge_semantics(tmp_path: Path) -> None:
    clean = tmp_path / "clean.db"
    bridge = tmp_path / "bridge.db"
    with SQLiteCatalog.create(clean):
        pass
    bridge_connection = get_connection(bridge)
    apply_candidate_migrations(bridge_connection, get_app_migrations_dir())
    bridge_connection.close()

    def core_schema(path: Path) -> list[tuple[object, ...]]:
        connection = _connect(path)
        try:
            return [
                tuple(row)
                for row in connection.execute(
                    "SELECT type,name,tbl_name,sql FROM sqlite_master "
                    "WHERE (name LIKE 'core_%' OR tbl_name LIKE 'core_%') "
                    "AND name NOT LIKE 'core_search_units_fts_%' "
                    "ORDER BY type,name"
                )
            ]
        finally:
            connection.close()

    assert core_schema(clean) == core_schema(bridge)
    connection = _connect(clean)
    try:
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name IN ('files','chunks','assets')"
        ).fetchone()[0] == 0
    finally:
        connection.close()


def test_create_fresh_reopen_readonly_foreign_keys_wal_and_checkpoint(tmp_path: Path) -> None:
    database = tmp_path / "clean.db"

    with SQLiteCatalog.create(database) as catalog:
        assert catalog.schema_id == SQLITE_CATALOG_SCHEMA_ID
        assert catalog.verify().schema_id == SQLITE_CATALOG_SCHEMA_ID
        assert catalog.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert catalog.connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        catalog.replace_resource(_batch())

    with SQLiteCatalog.open(database) as reopened:
        assert reopened.read_resource("resource") == _batch().resource
        assert reopened.verify().resources == 1

    with SQLiteCatalog.open_readonly(database) as readonly:
        assert readonly.schema_id == SQLITE_CATALOG_SCHEMA_ID
        assert readonly.read_resource("resource") == _batch().resource
        assert readonly.connection.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(CatalogExecutionError):
            readonly.delete_resource("resource")

    wal = database.with_name(database.name + "-wal")
    assert not wal.exists() or wal.stat().st_size == 0


def test_create_rejects_existing_empty_and_foreign_databases_without_mutation(tmp_path: Path) -> None:
    empty = tmp_path / "empty.db"
    empty.write_bytes(b"")
    with pytest.raises(SQLiteCatalogError) as existing:
        SQLiteCatalog.create(empty)
    assert existing.value.code is SQLiteErrorCode.DATABASE_EXISTS
    assert empty.read_bytes() == b""

    foreign = tmp_path / "PRIVATE_FOREIGN_SENTINEL.db"
    connection = sqlite3.connect(foreign)
    connection.execute("CREATE TABLE foreign_data(value TEXT)")
    connection.execute("INSERT INTO foreign_data VALUES('preserve-me')")
    connection.commit()
    connection.close()
    before = hashlib.sha256(foreign.read_bytes()).hexdigest()

    with pytest.raises(SQLiteCatalogError) as error:
        SQLiteCatalog.open(foreign)
    assert error.value.code is SQLiteErrorCode.SCHEMA_MISMATCH
    assert "PRIVATE_FOREIGN_SENTINEL" not in str(error.value)
    assert hashlib.sha256(foreign.read_bytes()).hexdigest() == before


def test_open_rejects_future_identity_and_schema_tamper(tmp_path: Path) -> None:
    future = tmp_path / "future.db"
    with SQLiteCatalog.create(future):
        pass
    connection = _connect(future)
    connection.execute("UPDATE mdrack_sqlite_schema SET schema_version='9999'")
    connection.commit()
    connection.close()

    with pytest.raises(SQLiteCatalogError) as future_error:
        SQLiteCatalog.open(future)
    assert future_error.value.code is SQLiteErrorCode.SCHEMA_MISMATCH

    tampered = tmp_path / "tampered.db"
    with SQLiteCatalog.create(tampered):
        pass
    connection = _connect(tampered)
    connection.execute("DROP INDEX idx_core_resources_hash")
    connection.commit()
    connection.close()

    with pytest.raises(SQLiteCatalogError) as tamper_error:
        SQLiteCatalog.open(tampered)
    assert tamper_error.value.code is SQLiteErrorCode.VERIFY_FAILED


@pytest.mark.parametrize(
    "drift_sql",
    [
        "CREATE TABLE PRIVATE_EXTRA_TABLE(value TEXT)",
        "ALTER TABLE core_resources ADD COLUMN PRIVATE_EXTRA_COLUMN TEXT",
        "CREATE INDEX PRIVATE_EXTRA_INDEX ON core_resources(title)",
        "CREATE TABLE core_search_units_fts_PRIVATE_EXTRA(value TEXT)",
        (
            "CREATE TRIGGER PRIVATE_EXTRA_TRIGGER AFTER UPDATE ON core_resources "
            "BEGIN SELECT 1; END"
        ),
    ],
    ids=[
        "extra-table",
        "changed-table-ddl",
        "extra-index",
        "fts-prefix-is-not-shadow",
        "extra-trigger",
    ],
)
def test_clean_schema_rejects_unmanifested_object_drift(
    tmp_path: Path,
    drift_sql: str,
) -> None:
    database = tmp_path / "drift.db"
    with SQLiteCatalog.create(database):
        pass
    connection = _connect(database)
    connection.execute(drift_sql)
    connection.commit()
    connection.close()

    for opener in (SQLiteCatalog.open, SQLiteCatalog.open_readonly):
        with pytest.raises(SQLiteCatalogError) as error:
            opener(database)
        assert error.value.code is SQLiteErrorCode.VERIFY_FAILED
        assert "PRIVATE_EXTRA" not in str(error.value)


def test_clean_schema_accounts_for_sqlite_internal_and_exact_fts_shadows(tmp_path: Path) -> None:
    database = tmp_path / "internals.db"
    with SQLiteCatalog.create(database):
        pass
    connection = _connect(database)
    try:
        assert {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE name LIKE 'core_search_units_fts_%'"
            )
        } == {
            "core_search_units_fts_config",
            "core_search_units_fts_content",
            "core_search_units_fts_data",
            "core_search_units_fts_docsize",
            "core_search_units_fts_idx",
        }
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name LIKE 'sqlite_autoindex_%'"
        ).fetchone()[0] > 0
    finally:
        connection.close()

    with SQLiteCatalog.open(database):
        pass
    with SQLiteCatalog.open_readonly(database):
        pass


def test_clean_ledger_rejects_unknown_or_tampered_rows(tmp_path: Path) -> None:
    database = tmp_path / "ledger.db"
    with SQLiteCatalog.create(database):
        pass
    connection = _connect(database)
    connection.execute(
        "UPDATE mdrack_sqlite_migrations SET sha256=? WHERE version='0002'",
        ("0" * 64,),
    )
    connection.commit()
    connection.close()

    with pytest.raises(SQLiteCatalogError) as error:
        SQLiteCatalog.open_readonly(database)
    assert error.value.code is SQLiteErrorCode.SCHEMA_MISMATCH


def test_corruption_fails_closed_at_open(tmp_path: Path) -> None:
    database = tmp_path / "corrupt.db"
    with SQLiteCatalog.create(database):
        pass
    payload = bytearray(database.read_bytes())
    payload[:16] = b"not a sqlite file"
    database.write_bytes(payload)

    with pytest.raises(SQLiteCatalogError) as error:
        SQLiteCatalog.open(database)
    assert error.value.code is SQLiteErrorCode.OPEN_FAILED


def test_failed_migration_is_atomic_and_exact_prefix_can_resume(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    copied = tmp_path / "migrations"
    shutil.copytree(get_migrations_dir(), copied)
    broken = copied / "0002_vectors_facets.sql"
    original = broken.read_bytes()
    broken.write_bytes(original + b"\nTHIS IS NOT SQL;\n")

    def install_manifest() -> None:
        entries = [(path.name, path.read_bytes()) for path in sorted(copied.glob("*.sql"))]
        monkeypatch.setattr(
            migration_module,
            "SQLITE_MIGRATION_MANIFEST",
            tuple((name, hashlib.sha256(content).hexdigest()) for name, content in entries),
        )
        monkeypatch.setattr(
            migration_module,
            "SQLITE_MIGRATION_MANIFEST_DIGEST",
            framed_manifest_digest(entries),
        )

    install_manifest()
    database = tmp_path / "interrupted.db"
    connection = _connect(database)
    with pytest.raises(SQLiteMigrationError):
        apply_migrations(connection, copied)
    assert [
        row[0]
        for row in connection.execute(
            "SELECT version FROM mdrack_sqlite_migrations ORDER BY version"
        )
    ] == ["0000", "0001"]
    assert connection.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE name='core_embedding_spaces'"
    ).fetchone()[0] == 0

    broken.write_bytes(original)
    install_manifest()
    apply_migrations(connection, copied)
    assert connection.execute(
        "SELECT schema_id FROM mdrack_sqlite_schema WHERE singleton=1"
    ).fetchone()[0] == SQLITE_CATALOG_SCHEMA_ID
    connection.close()


def test_write_lock_is_safe_and_concurrent_reader_sees_committed_graph(tmp_path: Path) -> None:
    database = tmp_path / "concurrency.db"
    with SQLiteCatalog.create(database) as seed:
        seed.replace_resource(_batch())

    writer = SQLiteCatalog.open(database)
    reader = SQLiteCatalog.open_readonly(database)
    contender = SQLiteCatalog.open(database, timeout=0.01)
    try:
        assert reader.read_resource("resource") == _batch().resource
        writer.connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(CatalogExecutionError) as error:
            contender.replace_resource(_batch("other"))
        assert str(error.value) == "adapter_timeout"
        assert reader.read_resource("resource") == _batch().resource
    finally:
        writer.close()
        reader.close()
        contender.close()
