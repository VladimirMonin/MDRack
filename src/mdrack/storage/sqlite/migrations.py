"""Custom migration runner for SQLite."""

from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
import struct
from pathlib import Path

logger = logging.getLogger(__name__)

_MIGRATION_PATTERN = re.compile(r"^(\d{4})_(.+)\.sql$")

EXPECTED_MIGRATION_VERSION = "0006"
EXPECTED_MIGRATION_MANIFEST: tuple[tuple[str, str], ...] = (
    ("0000_schema_migrations.sql", "9ae14a55be5ba6c6b6684e93173ab2d1ddb1cb498ef49b419d24e45ba389dc92"),
    ("0001_initial.sql", "878a7a644eec78b94eb0e9fc74f2285375eeeb52429a202048038c0e441fe786"),
    ("0002_fts.sql", "32f2896b8e043a08fcade1840730ca1efb0fc619871abf28fe6c3212a0d898a7"),
    ("0003_provenance.sql", "ae47c686c2849e947f4b48a683efb91a06c62577aba588ebe0feddfd0546d940"),
    ("0004_embedding_profiles.sql", "379b66e472f664b552689f866c0093151b181b07f000953c754a2bc0eee07937"),
    ("0005_assets.sql", "cc450db00514676ca328ded3c942afcb962a9b60c6fc6370b1890a9b70ec2400"),
    ("0006_complete_provenance.sql", "06aa00f1553285e51b96e2d5e0d331fe773703e737134ccb6e8a2dc835d5a69e"),
)
EXPECTED_MIGRATION_MANIFEST_DIGEST = "bd33d44185be1edb9bca9c2d82eed3b013f5ba8425b3d8ad98f0cf69c1e6a700"


class MigrationPlanError(RuntimeError):
    """Migration files or database versions do not form one safe linear history."""


def _framed_manifest_digest(entries: list[tuple[str, bytes]]) -> str:
    """Hash ordered filename/content pairs using the ``sha256-framed-v1`` contract."""
    digest = hashlib.sha256()
    for filename, content in entries:
        encoded_filename = filename.encode("utf-8")
        digest.update(struct.pack(">Q", len(encoded_filename)))
        digest.update(encoded_filename)
        digest.update(struct.pack(">Q", len(content)))
        digest.update(content)
    return digest.hexdigest()


def _migration_plan(migrations_dir: Path) -> list[tuple[str, Path]]:
    sql_files = sorted(migrations_dir.glob("*.sql"))
    plan: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for path in sql_files:
        match = _MIGRATION_PATTERN.fullmatch(path.name)
        if match is None:
            raise MigrationPlanError("migration filename does not match NNNN_name.sql")
        version = match.group(1)
        if version in seen:
            raise MigrationPlanError("duplicate migration version")
        seen.add(version)
        plan.append((version, path))
    expected = [f"{index:04d}" for index in range(len(plan))]
    if [version for version, _ in plan] != expected:
        raise MigrationPlanError("migration versions must be contiguous from 0000")
    return plan


def _validated_compiled_plan(migrations_dir: Path) -> list[tuple[str, Path, str]]:
    """Validate and freeze the exact compiled migration contents before SQL access."""
    plan = _migration_plan(migrations_dir)
    expected_names = [filename for filename, _ in EXPECTED_MIGRATION_MANIFEST]
    actual_names = [path.name for _, path in plan]
    if actual_names != expected_names:
        raise MigrationPlanError("migration package does not match compiled manifest")

    entries: list[tuple[str, bytes]] = []
    compiled_plan: list[tuple[str, Path, str]] = []
    for (version, path), (expected_name, expected_hash) in zip(plan, EXPECTED_MIGRATION_MANIFEST, strict=True):
        content = path.read_bytes()
        if path.name != expected_name or hashlib.sha256(content).hexdigest() != expected_hash:
            raise MigrationPlanError("migration package does not match compiled manifest")
        entries.append((path.name, content))
        compiled_plan.append((version, path, content.decode("utf-8")))
    if _framed_manifest_digest(entries) != EXPECTED_MIGRATION_MANIFEST_DIGEST:
        raise MigrationPlanError("migration package does not match compiled manifest digest")
    if plan[-1][0] != EXPECTED_MIGRATION_VERSION:
        raise MigrationPlanError("compiled migration version does not match manifest")
    return compiled_plan


def get_migrations_dir() -> Path:
    """Return the package-local directory containing SQL migrations."""
    return Path(__file__).resolve().with_name("migrations")


def _ensure_schema_migrations(conn: sqlite3.Connection) -> None:
    """Create the schema_migrations table if it does not exist."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.commit()


def get_applied_migrations(conn: sqlite3.Connection) -> set[str]:
    """Return the set of migration versions already applied.

    Args:
        conn: An open SQLite connection.

    Returns:
        Set of version strings (e.g., {"0000", "0001"}).
    """
    _ensure_schema_migrations(conn)
    cursor = conn.execute("SELECT version FROM schema_migrations")
    return {row["version"] for row in cursor.fetchall()}


def _validated_applied_migrations(conn: sqlite3.Connection) -> set[str]:
    cursor = conn.execute("SELECT version FROM schema_migrations")
    versions = [row["version"] for row in cursor.fetchall()]
    if any(not isinstance(version, str) for version in versions):
        raise MigrationPlanError("database migration ledger contains an invalid version")
    if len(versions) != len(set(versions)):
        raise MigrationPlanError("database migration ledger contains duplicate versions")

    available = [f"{index:04d}" for index in range(len(EXPECTED_MIGRATION_MANIFEST))]
    ordered = sorted(versions)
    if ordered != available[: len(ordered)]:
        raise MigrationPlanError("database migration ledger is unknown or non-contiguous")
    return set(versions)


def apply_migrations(conn: sqlite3.Connection, migrations_dir: Path) -> None:
    """Validate and apply the exact compiled migration package in version order.

    Package identity is validated before the connection is touched. Applied ledger
    versions must form a unique contiguous prefix of the compiled history.

    Args:
        conn: An open SQLite connection.
        migrations_dir: Directory containing the compiled .sql migration package.
    """
    plan = _validated_compiled_plan(migrations_dir)
    _ensure_schema_migrations(conn)
    applied = _validated_applied_migrations(conn)
    available = {version for version, _, _ in plan}
    if applied - available:
        raise MigrationPlanError("database contains migration versions unavailable to this build")
    pending = [(version, path, sql) for version, path, sql in plan if version not in applied]

    if not pending:
        logger.info("No pending migrations to apply")
        return

    for version, path, sql in pending:
        logger.info("Applying migration %s from %s", version, path.name)
        try:
            conn.executescript(
                "BEGIN IMMEDIATE;\n"
                f"{sql}\n"
                f"INSERT INTO schema_migrations (version) VALUES ('{version}');\n"
                "COMMIT;"
            )
            logger.info("Migration %s applied successfully", version)
        except Exception:
            conn.rollback()
            logger.exception("Migration %s failed, rolled back", version)
            raise
