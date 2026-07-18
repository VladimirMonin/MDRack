"""Immutable clean-package migration runner for ``mdrack_sqlite``."""

from __future__ import annotations

import hashlib
import re
import sqlite3
import struct
from collections.abc import Sequence
from pathlib import Path

from mdrack_sqlite.contract import (
    SQLITE_CATALOG_SCHEMA_ID,
    SQLITE_CATALOG_SCHEMA_VERSION,
    SQLITE_MIGRATION_MANIFEST,
    SQLITE_MIGRATION_MANIFEST_DIGEST,
)

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
_CLEAN_SCHEMA_FINGERPRINT = "af61c765ae1c965c33e7123053b0b4552b40532ee39c292ae6d7b33f5aeae69b"


class SQLiteMigrationError(RuntimeError):
    """A privacy-safe failure in package or database migration identity."""

    def __init__(self) -> None:
        super().__init__("migration_failed")


def get_migrations_dir() -> Path:
    """Return the immutable SQL history shipped inside the distribution."""
    return Path(__file__).resolve().with_name("migrations")


def framed_manifest_digest(entries: Sequence[tuple[str, bytes]]) -> str:
    """Hash ordered filename/content pairs using ``sha256-framed-v1``."""
    digest = hashlib.sha256()
    for filename, content in entries:
        encoded_filename = filename.encode("utf-8")
        digest.update(struct.pack(">Q", len(encoded_filename)))
        digest.update(encoded_filename)
        digest.update(struct.pack(">Q", len(content)))
        digest.update(content)
    return digest.hexdigest()


def _compiled_plan(migrations_dir: Path) -> list[tuple[str, str, str, str]]:
    try:
        paths = sorted(migrations_dir.glob("*.sql"))
        names = [path.name for path in paths]
        if names != [name for name, _digest in SQLITE_MIGRATION_MANIFEST]:
            raise SQLiteMigrationError
        plan: list[tuple[str, str, str, str]] = []
        entries: list[tuple[str, bytes]] = []
        for path, (expected_name, expected_digest) in zip(
            paths, SQLITE_MIGRATION_MANIFEST, strict=True
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
        expected_versions = [f"{index:04d}" for index in range(len(plan))]
        if [version for version, _name, _digest, _sql in plan] != expected_versions:
            raise SQLiteMigrationError
        if not plan or plan[-1][0] != SQLITE_CATALOG_SCHEMA_VERSION:
            raise SQLiteMigrationError
        if framed_manifest_digest(entries) != SQLITE_MIGRATION_MANIFEST_DIGEST:
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
    """Fingerprint canonical objects while accounting for SQLite-owned objects."""
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
        if name.startswith("sqlite_"):
            continue
        if (object_type, name, table_name) in _FTS5_SHADOW_OBJECTS:
            continue
        if object_type not in _SCHEMA_OBJECT_TYPES or not isinstance(sql, str):
            raise SQLiteMigrationError
        normalized_sql = " ".join(sql.split())
        for field in (object_type, name, table_name, normalized_sql):
            encoded = field.encode("utf-8")
            digest.update(struct.pack(">Q", len(encoded)))
            digest.update(encoded)
    return digest.hexdigest()


def validate_clean_schema(connection: sqlite3.Connection) -> None:
    """Reject added, changed, or removed canonical clean-schema objects."""
    try:
        if _schema_fingerprint(connection) != _CLEAN_SCHEMA_FINGERPRINT:
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
            SQLITE_CATALOG_SCHEMA_ID,
            SQLITE_CATALOG_SCHEMA_VERSION,
            SQLITE_MIGRATION_MANIFEST_DIGEST,
        ):
            raise SQLiteMigrationError
        if identity and len(rows) != len(plan):
            raise SQLiteMigrationError
        return len(rows)
    except SQLiteMigrationError:
        raise
    except Exception:
        raise SQLiteMigrationError from None


def apply_migrations(
    connection: sqlite3.Connection,
    migrations_dir: Path | None = None,
) -> None:
    """Apply or resume the exact clean history on an empty or recognized partial DB.

    Package bytes are validated before database access. Every SQL migration and its
    ledger row share one ``BEGIN IMMEDIATE`` transaction. A foreign, future, gapped,
    or tampered database fails closed without schema mutation.
    """
    if not isinstance(connection, sqlite3.Connection) or connection.in_transaction:
        raise SQLiteMigrationError
    plan = _compiled_plan(get_migrations_dir() if migrations_dir is None else migrations_dir)
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
                SQLITE_CATALOG_SCHEMA_ID,
                SQLITE_CATALOG_SCHEMA_VERSION,
                SQLITE_MIGRATION_MANIFEST_DIGEST,
            ),
        )
        connection.commit()
        validate_clean_identity(connection)
    except SQLiteMigrationError:
        connection.rollback()
        raise
    except Exception:
        connection.rollback()
        raise SQLiteMigrationError from None


def validate_clean_identity(connection: sqlite3.Connection) -> None:
    """Validate the exact complete clean ledger and schema identity without mutation."""
    plan = _compiled_plan(get_migrations_dir())
    if _validated_applied_prefix(connection, plan) != len(plan):
        raise SQLiteMigrationError
    row = connection.execute(
        "SELECT schema_id,schema_version,manifest_digest FROM mdrack_sqlite_schema "
        "WHERE singleton=1"
    ).fetchone()
    if row is None or tuple(row) != (
        SQLITE_CATALOG_SCHEMA_ID,
        SQLITE_CATALOG_SCHEMA_VERSION,
        SQLITE_MIGRATION_MANIFEST_DIGEST,
    ):
        raise SQLiteMigrationError
