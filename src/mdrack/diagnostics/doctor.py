"""Diagnostic checks for knowledge store health."""

from __future__ import annotations

import dataclasses
import re
import sqlite3
from pathlib import Path

from mdrack.diagnostics.integrity import get_generation_status, get_store_status
from mdrack.embeddings.hashing import hash_embedding_text
from mdrack.storage.sqlite.migrations import get_applied_migrations, get_migrations_dir

_FIXED_MESSAGES = {
    "DATABASE_NOT_FOUND": "Knowledge store database was not found",
    "GENERATION_POINTER_INVALID": "The active store generation pointer is invalid",
    "GENERATION_POINTER_MISSING": "The active store generation pointer is missing",
    "GENERATION_BUILDING": "A store generation rebuild is incomplete",
    "GENERATION_FAILED": "A store generation rebuild failed",
    "GENERATION_READY": "The active store generation is ready",
    "GENERATION_LEGACY": "The active store uses the legacy contract",
    "STATUS_ERROR": "Store status could not be read",
    "PROFILE_METADATA_MISSING": "Embedding profile metadata is missing",
    "PROFILE_METADATA_PRESENT": "Embedding profile metadata is present",
    "PROFILE_CONFIG_MISMATCH": "Embedding profile metadata does not match the current configuration",
    "PROFILE_CONFIG_MATCH": "Embedding profile metadata matches the current configuration",
    "MISSING_FTS": "Chunks are missing from the FTS index",
    "FTS_OK": "All chunks have FTS entries",
    "MISSING_EMBEDDINGS": "Chunks are missing embedding vectors",
    "EMBEDDINGS_OK": "All chunks with embedding text have vectors",
    "STALE_EMBEDDINGS": "Embedding vectors do not match their source text",
    "EMBEDDINGS_FRESH": "All embedding vectors match their source text",
    "SCHEMA_BEHIND": "The database schema is behind the packaged schema",
    "SCHEMA_MISSING": "Packaged migrations are missing from the applied ledger",
    "SCHEMA_LATEST": "The database schema is up to date",
    "SCHEMA_CHECK_ERROR": "Schema migrations could not be checked",
}
_SAFE_DETAIL_KEYS = frozenset(
    {
        "reason_code",
        "generation_state",
        "count",
        "profile",
        "configured_model",
        "profile_model",
        "configured_dimensions",
        "profile_dimensions",
        "endpoint_match",
        "missing_fts_count",
        "fts_count",
        "chunk_count",
        "missing_count",
        "chunks_with_text",
        "embeddings_found",
        "stale_count",
        "applied_versions",
        "file_versions",
        "missing_versions",
        "future_versions",
    }
)


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


def report_to_dict(report: DoctorReport) -> dict[str, object]:
    """Convert a doctor report into a stable JSON-safe structure."""
    summary = {
        "total": len(report.findings),
        "errors": sum(1 for finding in report.findings if finding.severity == "error"),
        "warnings": sum(1 for finding in report.findings if finding.severity == "warning"),
        "info": sum(1 for finding in report.findings if finding.severity == "info"),
    }
    return {
        "ok": report.ok,
        "summary": summary,
        "findings": [
            {
                "severity": finding.severity,
                "code": finding.code,
                "message": _FIXED_MESSAGES.get(finding.code, "Diagnostic check completed"),
                "details": {
                    key: value
                    for key, value in finding.details.items()
                    if key in _SAFE_DETAIL_KEYS
                },
            }
            for finding in report.findings
        ],
    }


def _get_active_profile(conn: sqlite3.Connection) -> str:
    """Determine the active embedding profile.

    Returns the first profile from embedding_profiles, or 'default' if none exist.
    """
    cursor = conn.execute("SELECT name FROM embedding_profiles ORDER BY name LIMIT 1")
    row = cursor.fetchone()
    if row:
        return row["name"]
    return "default"


def run_doctor(
    conn: sqlite3.Connection,
    expected_profile: str = "default",
    expected_model: str | None = None,
    expected_dimensions: int | None = None,
    expected_endpoint: str | None = None,
    store_dir: Path | None = None,
) -> DoctorReport:
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

    if store_dir is not None:
        generation = get_generation_status(store_dir)
        generation_state = str(generation["generation_state"])
        pointer_status = str(generation["generation_pointer_status"])
        if pointer_status == "invalid":
            findings.append(
                DoctorFinding(
                    severity="error",
                    code="GENERATION_POINTER_INVALID",
                    message="The active store generation pointer is invalid",
                    details={"generation_state": "failed", "reason_code": "pointer_invalid"},
                )
            )
        elif pointer_status == "missing" and generation["generation_metadata_count"]:
            findings.append(
                DoctorFinding(
                    severity="error",
                    code="GENERATION_POINTER_MISSING",
                    message="The active store generation pointer is missing",
                    details={"generation_state": "failed", "reason_code": "pointer_missing"},
                )
            )
        elif generation_state == "building":
            findings.append(
                DoctorFinding(
                    severity="warning",
                    code="GENERATION_BUILDING",
                    message="A store generation rebuild is incomplete",
                    details={
                        "generation_state": generation_state,
                        "count": generation["generation_building_count"],
                    },
                )
            )
        elif generation_state == "failed":
            findings.append(
                DoctorFinding(
                    severity="warning",
                    code="GENERATION_FAILED",
                    message="A store generation rebuild failed",
                    details={
                        "generation_state": generation_state,
                        "count": generation["generation_failed_count"],
                    },
                )
            )
        else:
            findings.append(
                DoctorFinding(
                    severity="info",
                    code="GENERATION_READY" if generation_state == "ready" else "GENERATION_LEGACY",
                    message=(
                        "The active store generation is ready"
                        if generation_state == "ready"
                        else "The active store uses the legacy contract"
                    ),
                    details={"generation_state": generation_state},
                )
            )

    try:
        status = get_store_status(conn, profile_name=expected_profile)
    except Exception:
        findings.append(
            DoctorFinding(
                severity="error",
                code="STATUS_ERROR",
                message="Store status could not be read",
                details={"reason_code": "status_unavailable"},
            )
        )
        return DoctorReport(findings=findings, ok=False)

    chunks_count = status.get("chunks_count", 0)
    active_profile = expected_profile or _get_active_profile(conn)

    profile_model = status.get("profile_model")
    profile_dimensions = status.get("profile_dimensions")
    profile_endpoint = status.get("profile_endpoint")

    if profile_model is None:
        findings.append(
            DoctorFinding(
                severity="warning",
                code="PROFILE_METADATA_MISSING",
                message=(
                    f'Embedding profile "{active_profile}" has no stored metadata. '
                    "Run 'mdrack rebuild embeddings' to repair it."
                ),
                details={"profile": active_profile},
            )
        )
    else:
        findings.append(
            DoctorFinding(
                severity="info",
                code="PROFILE_METADATA_PRESENT",
                message=f'Embedding profile "{active_profile}" metadata is present',
                details={
                    "profile": active_profile,
                    "model": profile_model,
                    "dimensions": profile_dimensions,
                    "endpoint": profile_endpoint,
                },
            )
        )

    mismatch_details: dict[str, object] = {"profile": active_profile}
    has_mismatch = False
    if expected_model is not None and profile_model != expected_model:
        mismatch_details["configured_model"] = expected_model
        mismatch_details["profile_model"] = profile_model
        has_mismatch = True
    if expected_dimensions is not None and profile_dimensions != expected_dimensions:
        mismatch_details["configured_dimensions"] = expected_dimensions
        mismatch_details["profile_dimensions"] = profile_dimensions
        has_mismatch = True
    if expected_endpoint is not None and profile_endpoint != expected_endpoint:
        mismatch_details["endpoint_match"] = False
        has_mismatch = True

    if has_mismatch:
        findings.append(
            DoctorFinding(
                severity="warning",
                code="PROFILE_CONFIG_MISMATCH",
                message=(
                    f'Embedding profile "{active_profile}" metadata does not match '
                    "the current MDRack configuration"
                ),
                details=mismatch_details,
            )
        )
    elif profile_model is not None and expected_model is not None:
        findings.append(
            DoctorFinding(
                severity="info",
                code="PROFILE_CONFIG_MATCH",
                message=(
                    f'Embedding profile "{active_profile}" metadata matches '
                    "the current MDRack configuration"
                ),
                details={
                    "profile": active_profile,
                    "model": profile_model,
                    "dimensions": profile_dimensions,
                    "endpoint": profile_endpoint,
                },
            )
        )

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
        applied = get_applied_migrations(conn)
        migrations_dir = get_migrations_dir()

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
    except Exception:
        findings.append(
            DoctorFinding(
                severity="error",
                code="SCHEMA_CHECK_ERROR",
                message="Schema migrations could not be checked",
                details={"reason_code": "schema_check_failed"},
            )
        )

    ok = all(f.severity != "error" for f in findings)
    return DoctorReport(findings=findings, ok=ok)
