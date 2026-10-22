"""Explicit offline maintenance for the one recognized completed old v2."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable
from pathlib import Path

from .contract_v2 import (
    SQLITE_CATALOG_V2_SCHEMA_ID,
    SQLITE_CATALOG_V2_SCHEMA_VERSION,
    SQLITE_V2_MIGRATION_MANIFEST_DIGEST,
)
from .errors import SQLiteCatalogError, SQLiteErrorCode
from .migrations import SQLiteMigrationError
from .migrations_v2 import (
    _compiled_plan,
    _schema_fingerprint,
    get_v2_migrations_dir,
    validate_v2_clean_identity,
)

_OLD_DIGEST = "353facc85a46cfc30fea8511993e06aa49910b09d60fe7750cb1855df120dd98"
_OLD_FINGERPRINT = "854fd964b5fc6e3018205e562d49b4ec0dcb20be3b5d3428297a557f9ae06c4b"


def _verify_old(connection: sqlite3.Connection) -> None:
    plan = _compiled_plan(get_v2_migrations_dir())
    if [
        tuple(r)
        for r in connection.execute("SELECT schema_id,schema_version,manifest_digest FROM mdrack_sqlite_schema")
    ] != [(SQLITE_CATALOG_V2_SCHEMA_ID, "0004", _OLD_DIGEST)]:
        raise SQLiteMigrationError
    if [
        tuple(r)
        for r in connection.execute("SELECT version,name,sha256 FROM mdrack_sqlite_migrations ORDER BY version")
    ] != [(v, n, h) for v, n, h, _sql in plan[:5]]:
        raise SQLiteMigrationError
    if _schema_fingerprint(connection) != _OLD_FINGERPRINT:
        raise SQLiteMigrationError
    if [r[0] for r in connection.execute("PRAGMA integrity_check")] != ["ok"]:
        raise SQLiteMigrationError
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise SQLiteMigrationError
    # Verify complete graph, codecs, FTS and all prior invariants, not just DDL.
    from .catalog import SQLiteCatalog

    catalog = SQLiteCatalog(connection, schema_id=SQLITE_CATALOG_V2_SCHEMA_ID)
    catalog._verify_graph_snapshot()


def upgrade_v2_file(
    database_path: str | Path,
    backup_path: str | Path,
    *,
    maintenance: bool,
    failure_hook: Callable[[str], None] | None = None,
) -> bool:
    """Return changed; host must stop and close ALL consumers before calling.

    Never called by normal open. Backup must be an absent file outside the store.
    A pre-commit failure preserves old data; an uncertain commit requires reopen.
    """
    if maintenance is not True:
        raise SQLiteCatalogError(SQLiteErrorCode.MIGRATION_FAILED)
    reader: sqlite3.Connection | None = None
    writer: sqlite3.Connection | None = None
    backup: sqlite3.Connection | None = None
    reserved = False
    try:
        path = Path(database_path).resolve(strict=True)
        destination = Path(backup_path).resolve(strict=False)
        if destination.is_relative_to(path.parent):
            raise ValueError
        reader = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
        reader.execute("PRAGMA foreign_keys=ON")
        reader.execute("BEGIN")
        try:
            validate_v2_clean_identity(reader)
        except SQLiteMigrationError:
            _verify_old(reader)
        else:
            from .catalog import SQLiteCatalog

            SQLiteCatalog(reader, readonly=True, schema_id=SQLITE_CATALOG_V2_SCHEMA_ID)._verify_snapshot()
            return False
        if not destination.parent.is_dir() or destination.exists():
            raise ValueError
        fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        reserved = True
        backup = sqlite3.connect(destination)
        backup.execute("PRAGMA foreign_keys=ON")
        reader.backup(backup)
        _verify_old(backup)
        backup.close()
        backup = None
        reader.rollback()
        reader.close()
        reader = None
        writer = sqlite3.connect(f"{path.as_uri()}?mode=rw", uri=True)
        writer.execute("PRAGMA foreign_keys=ON")
        writer.execute("PRAGMA synchronous=FULL")
        writer.execute("BEGIN IMMEDIATE")
        _verify_old(writer)
        if failure_hook is not None:
            failure_hook("after_upgrade_begin")
        version, name, digest, sql = _compiled_plan(get_v2_migrations_dir())[-1]
        statement = ""
        for line in sql.splitlines(keepends=True):
            statement += line
            if sqlite3.complete_statement(statement):
                writer.execute(statement)
                statement = ""
        if statement.strip():
            raise SQLiteMigrationError
        if failure_hook is not None:
            failure_hook("after_upgrade_state")
        writer.execute(
            "INSERT INTO mdrack_sqlite_migrations(version,name,sha256) VALUES(?,?,?)", (version, name, digest)
        )
        writer.execute(
            "UPDATE mdrack_sqlite_schema SET schema_version=?,manifest_digest=? WHERE singleton=1",
            (SQLITE_CATALOG_V2_SCHEMA_VERSION, SQLITE_V2_MIGRATION_MANIFEST_DIGEST),
        )
        validate_v2_clean_identity(writer)
        from .catalog import SQLiteCatalog

        SQLiteCatalog(writer, schema_id=SQLITE_CATALOG_V2_SCHEMA_ID)._verify_snapshot()
        if failure_hook is not None:
            failure_hook("before_upgrade_commit")
        writer.commit()
        if failure_hook is not None:
            failure_hook("after_upgrade_commit")
        writer.close()
        writer = None
        with SQLiteCatalog.open(path) as reopened:
            reopened.verify()
        return True
    except BaseException as error:
        if writer is not None:
            writer.rollback()
        if not isinstance(error, Exception):
            raise
        raise SQLiteCatalogError(SQLiteErrorCode.MIGRATION_FAILED) from None
    finally:
        for connection in (reader, writer, backup):
            if connection is not None:
                connection.close()
        # A verified backup is retained even on upgrade failure; never remove the source.
        del reserved
