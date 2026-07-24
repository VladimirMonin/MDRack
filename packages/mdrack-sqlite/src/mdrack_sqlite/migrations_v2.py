"""Immutable v2 clean-package migration runner for fresh compact catalogs."""

from __future__ import annotations

import hashlib
import re
import sqlite3
import struct
from collections.abc import Sequence
from pathlib import Path

from mdrack_sqlite.contract_v2 import (
    SQLITE_CATALOG_V2_SCHEMA_ID,
    SQLITE_CATALOG_V2_SCHEMA_VERSION,
    SQLITE_V2_MIGRATION_MANIFEST,
    SQLITE_V2_MIGRATION_MANIFEST_DIGEST,
)
from mdrack_sqlite.migrations import SQLiteMigrationError, framed_manifest_digest

_MIGRATION_PATTERN = re.compile(r"^(\d{4})_(.+)\.sql$")
_SCHEMA_OBJECT_TYPES = ("table", "view", "index", "trigger")
_FTS5_SHADOW_OBJECTS = frozenset(
    {
        ("table", "core_search_units_fts_config", "core_search_units_fts_config"),
        ("table", "core_search_units_fts_content", "core_search_units_fts_content"),
        ("table", "core_search_units_fts_data", "core_search_units_fts_data"),
        ("table", "core_search_units_fts_docsize", "core_search_units_fts_docsize"),
        ("table", "core_search_units_fts_idx", "core_search_units_fts_idx"),
    }
)
_FTS5_SHADOW_NAMES = frozenset(name for _type, name, _table in _FTS5_SHADOW_OBJECTS)
_CLEAN_V2_SCHEMA_FINGERPRINT = "854fd964b5fc6e3018205e562d49b4ec0dcb20be3b5d3428297a557f9ae06c4b"


def get_v2_migrations_dir() -> Path:
    """Return the independent SQL history for fresh v2 databases."""
    return Path(__file__).resolve().with_name("v2_migrations")


def _compiled_plan(migrations_dir: Path) -> list[tuple[str, str, str, str]]:
    try:
        paths = sorted(migrations_dir.glob("*.sql"))
        if [path.name for path in paths] != [name for name, _digest in SQLITE_V2_MIGRATION_MANIFEST]:
            raise SQLiteMigrationError
        entries: list[tuple[str, bytes]] = []
        plan: list[tuple[str, str, str, str]] = []
        for path, (expected_name, expected_digest) in zip(
            paths, SQLITE_V2_MIGRATION_MANIFEST, strict=True
        ):
            match = _MIGRATION_PATTERN.fullmatch(path.name)
            content = path.read_bytes()
            if (
                match is None
                or path.name != expected_name
                or hashlib.sha256(content).hexdigest() != expected_digest
            ):
                raise SQLiteMigrationError
            entries.append((path.name, content))
            plan.append((match.group(1), path.name, expected_digest, content.decode("utf-8")))
        if [version for version, _name, _digest, _sql in plan] != [
            f"{index:04d}" for index in range(len(plan))
        ]:
            raise SQLiteMigrationError
        if not plan or plan[-1][0] != SQLITE_CATALOG_V2_SCHEMA_VERSION:
            raise SQLiteMigrationError
        if framed_manifest_digest(entries) != SQLITE_V2_MIGRATION_MANIFEST_DIGEST:
            raise SQLiteMigrationError
        return plan
    except SQLiteMigrationError:
        raise
    except Exception:
        raise SQLiteMigrationError from None


def _object_names(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type IN ('table','view','index','trigger') AND name NOT LIKE 'sqlite_%'"
        )
    }


def _schema_fingerprint(connection: sqlite3.Connection) -> str:
    rows = [
        (str(row[0]), str(row[1]), str(row[2]), row[3])
        for row in connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master "
            "WHERE type IN ('table','view','index','trigger') ORDER BY type,name"
        )
    ]
    actual_fts_shadows = {
        (object_type, name, table_name)
        for object_type, name, table_name, _sql in rows
        if name in _FTS5_SHADOW_NAMES
    }
    if actual_fts_shadows != _FTS5_SHADOW_OBJECTS:
        raise SQLiteMigrationError
    digest = hashlib.sha256()
    for object_type, name, table_name, sql in rows:
        if name.startswith("sqlite_") or (object_type, name, table_name) in _FTS5_SHADOW_OBJECTS:
            continue
        if object_type not in _SCHEMA_OBJECT_TYPES or not isinstance(sql, str):
            raise SQLiteMigrationError
        normalized_sql = " ".join(sql.split())
        for field in (object_type, name, table_name, normalized_sql):
            encoded = field.encode("utf-8")
            digest.update(struct.pack(">Q", len(encoded)))
            digest.update(encoded)
    return digest.hexdigest()


def validate_v2_clean_schema(connection: sqlite3.Connection) -> None:
    """Reject changed, missing, or added v2 objects."""
    try:
        if _schema_fingerprint(connection) != _CLEAN_V2_SCHEMA_FINGERPRINT:
            raise SQLiteMigrationError
    except SQLiteMigrationError:
        raise
    except Exception:
        raise SQLiteMigrationError from None


def _validated_applied_prefix(
    connection: sqlite3.Connection,
    plan: Sequence[tuple[str, str, str, str]],
) -> int:
    objects = _object_names(connection)
    if not objects:
        return 0
    if "mdrack_sqlite_migrations" not in objects or "mdrack_sqlite_schema" not in objects:
        raise SQLiteMigrationError
    try:
        rows = [
            tuple(row)
            for row in connection.execute(
                "SELECT version,name,sha256 FROM mdrack_sqlite_migrations ORDER BY version"
            )
        ]
        expected = [(version, name, digest) for version, name, digest, _sql in plan]
        if rows != expected[: len(rows)]:
            raise SQLiteMigrationError
        identity = connection.execute(
            "SELECT schema_id,schema_version,manifest_digest FROM mdrack_sqlite_schema "
            "WHERE singleton=1"
        ).fetchall()
        if len(identity) > 1:
            raise SQLiteMigrationError
        if identity and tuple(identity[0]) != (
            SQLITE_CATALOG_V2_SCHEMA_ID,
            SQLITE_CATALOG_V2_SCHEMA_VERSION,
            SQLITE_V2_MIGRATION_MANIFEST_DIGEST,
        ):
            raise SQLiteMigrationError
        if identity and len(rows) != len(plan):
            raise SQLiteMigrationError
        return len(rows)
    except SQLiteMigrationError:
        raise
    except Exception:
        raise SQLiteMigrationError from None


def apply_v2_migrations(
    connection: sqlite3.Connection,
    migrations_dir: Path | None = None,
) -> None:
    """Create only a fresh or recognized partial v2 catalog; never upgrade v1."""
    if not isinstance(connection, sqlite3.Connection) or connection.in_transaction:
        raise SQLiteMigrationError
    plan = _compiled_plan(get_v2_migrations_dir() if migrations_dir is None else migrations_dir)
    applied_count = _validated_applied_prefix(connection, plan)
    try:
        for version, name, digest, sql in plan[applied_count:]:
            connection.executescript(
                "BEGIN IMMEDIATE;\n"
                f"{sql}\n"
                "INSERT INTO mdrack_sqlite_migrations(version,name,sha256) "
                f"VALUES('{version}','{name}','{digest}');\n"
                "COMMIT;"
            )
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO mdrack_sqlite_schema(singleton,schema_id,schema_version,manifest_digest) "
            "VALUES(1,?,?,?) ON CONFLICT(singleton) DO NOTHING",
            (
                SQLITE_CATALOG_V2_SCHEMA_ID,
                SQLITE_CATALOG_V2_SCHEMA_VERSION,
                SQLITE_V2_MIGRATION_MANIFEST_DIGEST,
            ),
        )
        connection.commit()
        validate_v2_clean_identity(connection)
    except SQLiteMigrationError:
        connection.rollback()
        raise
    except Exception:
        connection.rollback()
        raise SQLiteMigrationError from None


def validate_v2_clean_identity(connection: sqlite3.Connection) -> None:
    """Validate complete v2 ledger, identity, and canonical object fingerprint."""
    plan = _compiled_plan(get_v2_migrations_dir())
    if _validated_applied_prefix(connection, plan) != len(plan):
        raise SQLiteMigrationError
    row = connection.execute(
        "SELECT schema_id,schema_version,manifest_digest FROM mdrack_sqlite_schema "
        "WHERE singleton=1"
    ).fetchone()
    if row is None or tuple(row) != (
        SQLITE_CATALOG_V2_SCHEMA_ID,
        SQLITE_CATALOG_V2_SCHEMA_VERSION,
        SQLITE_V2_MIGRATION_MANIFEST_DIGEST,
    ):
        raise SQLiteMigrationError
    validate_v2_clean_schema(connection)
