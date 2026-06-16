"""Diagnostic checks for knowledge store health."""

from __future__ import annotations

import dataclasses
import re
import sqlite3
from pathlib import Path

from mdrack.diagnostics.integrity import get_store_status
from mdrack.embeddings.hashing import hash_embedding_text
from mdrack.storage.sqlite.migrations import get_applied_migrations


@dataclasses.dataclass
class DoctorFinding:
    """A single diagnostic finding."""

    severity: str  # 'error', 'warning', 'info'
    code: str
    message: str
    details: dict[str, object] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass
class DoctorReport:
    """Complete diagnostic report from the doctor command."""

    findings: list[DoctorFinding]
    ok: bool


def _get_active_profile(conn: sqlite3.Connection) -> str:
    """Determine the active embedding profile.

    Returns the first profile from embedding_profiles, or 'default' if none exist.
    """
    cursor = conn.execute("SELECT name FROM embedding_profiles ORDER BY name LIMIT 1")
    row = cursor.fetchone()
    if row:
        return row["name"]
    return "default"


def run_doctor(conn: sqlite3.Connection) -> DoctorReport:
    """Run all diagnostic checks on the knowledge store.

    Checks performed:
        1. Missing FTS rows
        2. Missing embeddings for the active profile
        3. Stale embeddings (hash mismatch)
        4. Schema version (migrations applied vs available)

    Args:
        conn: An open SQLite connection.

    Returns:
        DoctorReport with all findings and an overall ok flag.
    """
    findings: list[DoctorFinding] = []

    try:
        status = get_store_status(conn)
    except Exception as exc:
        findings.append(
            DoctorFinding(
                severity="error",
                code="STATUS_ERROR",
                message=f"Failed to get store status: {exc}",
            )
        )
        return DoctorReport(findings=findings, ok=False)

    chunks_count = status.get("chunks_count", 0)
    active_profile = _get_active_profile(conn)

    # Check 1: Missing FTS rows
    cursor = conn.execute(
        """
        SELECT COUNT(*) AS missing_count
        FROM chunks c
        LEFT JOIN chunks_fts f ON c.id = f.chunk_id
        WHERE f.chunk_id IS NULL
        """
    )
    missing_fts = cursor.fetchone()["missing_count"]

    if missing_fts > 0:
        findings.append(
            DoctorFinding(
                severity="error",
                code="MISSING_FTS",
                message=f"{missing_fts} chunks are missing from the FTS index",
                details={"missing_fts_count": int(missing_fts)},
            )
        )
    else:
        findings.append(
            DoctorFinding(
                severity="info",
                code="FTS_OK",
                message="All chunks have FTS entries",
                details={
                    "fts_count": int(chunks_count),
                    "chunk_count": int(chunks_count),
                },
            )
        )

    # Check 2: Missing embeddings for active profile
    cursor = conn.execute(
        """
        SELECT COUNT(*) AS missing_count
        FROM chunks c
        LEFT JOIN chunk_embeddings ce ON c.id = ce.chunk_id AND ce.profile_name = ?
        WHERE c.embedding_text IS NOT NULL AND ce.chunk_id IS NULL
        """,
        (active_profile,),
    )
    missing_embeddings = cursor.fetchone()["missing_count"]

    cursor2 = conn.execute(
        "SELECT COUNT(*) AS count FROM chunks WHERE embedding_text IS NOT NULL"
    )
    chunks_with_embedding_text = cursor2.fetchone()["count"]

    cursor3 = conn.execute(
        "SELECT COUNT(*) AS count FROM chunk_embeddings WHERE profile_name = ?",
        (active_profile,),
    )
    embeddings_for_profile = cursor3.fetchone()["count"]

    if missing_embeddings > 0:
        findings.append(
            DoctorFinding(
                severity="warning",
                code="MISSING_EMBEDDINGS",
                message=(
                    f"{missing_embeddings} chunks have embedding_text "
                    f'but no vectors in profile "{active_profile}"'
                ),
                details={
                    "missing_count": int(missing_embeddings),
                    "profile": active_profile,
                    "chunks_with_text": int(chunks_with_embedding_text),
                    "embeddings_found": int(embeddings_for_profile),
                },
            )
        )
    else:
        findings.append(
            DoctorFinding(
                severity="info",
                code="EMBEDDINGS_OK",
                message=(
                    "All chunks with embedding_text have vectors "
                    f'for profile "{active_profile}"'
                ),
                details={
                    "profile": active_profile,
                    "chunks_with_text": int(chunks_with_embedding_text),
                    "embeddings_found": int(embeddings_for_profile),
                },
            )
        )

    # Check 3: Stale embeddings (hash mismatch)
    cursor = conn.execute(
        """
        SELECT c.id, c.embedding_text, c.embedding_text_hash
        FROM chunks c
        WHERE c.embedding_text IS NOT NULL
        """
    )
    stale_count = 0
    for row in cursor.fetchall():
        stored_hash = row["embedding_text_hash"]
        if stored_hash:
            current_hash = hash_embedding_text(row["embedding_text"])
            if current_hash != stored_hash:
                stale_count += 1

    if stale_count > 0:
        findings.append(
            DoctorFinding(
                severity="warning",
                code="STALE_EMBEDDINGS",
                message=(
                    f"{stale_count} chunks have embedding vectors "
                    f"that do not match current embedding_text"
                ),
                details={"stale_count": stale_count},
            )
        )
    elif chunks_with_embedding_text > 0:
        findings.append(
            DoctorFinding(
                severity="info",
                code="EMBEDDINGS_FRESH",
                message="All embedding vectors match their source text",
                details={},
            )
        )

    # Check 4: Schema version check
    try:
        import mdrack.storage.sqlite.migrations as migrations_mod

        applied = get_applied_migrations(conn)
        migrations_dir = Path(migrations_mod.__file__).parent / "migrations"

        file_versions: set[str] = set()
        for path in sorted(migrations_dir.glob("*.sql")):
            m = re.match(r"(\d{4})_.*\.sql$", path.name)
            if m:
                file_versions.add(m.group(1))

        missing_versions = file_versions - applied
        future_versions = set()
        if applied:
            max_applied = max(int(v) for v in applied)
        else:
            max_applied = -1
        future_versions = {v for v in file_versions if int(v) > max_applied}

        if missing_versions:
            if future_versions:
                findings.append(
                    DoctorFinding(
                        severity="warning",
                        code="SCHEMA_BEHIND",
                        message=(
                            f"Schema is behind: {len(missing_versions)} migration(s) "
                            f"not applied, {len(future_versions)} future migration(s) exist"
                        ),
                        details={
                            "applied_versions": sorted(applied, key=int),
                            "file_versions": sorted(file_versions, key=int),
                            "missing_versions": sorted(missing_versions, key=int),
                            "future_versions": sorted(future_versions, key=int),
                        },
                    )
                )
            else:
                findings.append(
                    DoctorFinding(
                        severity="warning",
                        code="SCHEMA_MISSING",
                        message=(
                            f"{len(missing_versions)} migration(s) are not "
                            "in applied list despite existing files"
                        ),
                        details={
                            "applied_versions": sorted(applied, key=int),
                            "file_versions": sorted(file_versions, key=int),
                            "missing_versions": sorted(missing_versions, key=int),
                        },
                    )
                )
        else:
            findings.append(
                DoctorFinding(
                    severity="info",
                    code="SCHEMA_LATEST",
                    message=(
                        f"Schema is up-to-date "
                        f"(applied migrations: {sorted(applied, key=int)})"
                    ),
                    details={
                        "applied_versions": sorted(applied, key=int),
                    },
                )
            )
    except Exception as exc:
        findings.append(
            DoctorFinding(
                severity="error",
                code="SCHEMA_CHECK_ERROR",
                message=f"Failed to check schema migrations: {exc}",
                details={},
            )
        )

    ok = all(f.severity != "error" for f in findings)
    return DoctorReport(findings=findings, ok=ok)
