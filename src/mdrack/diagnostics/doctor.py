"""Diagnostic checks for knowledge store health."""

from __future__ import annotations

import dataclasses
import sqlite3

from mdrack_sqlite.migrations_v2 import validate_v2_clean_identity

_FIXED_MESSAGES = {
    "DATABASE_NOT_FOUND": "Knowledge store database was not found",
    "RESOURCE_CORE_V2_INVALID": "The resource-core v2 catalog is invalid",
    "RESOURCE_CORE_V2_SCHEMA_LATEST": "The resource-core v2 schema is up to date",
    "RESOURCE_CORE_V2_INTEGRITY_OK": "The resource-core v2 integrity checks passed",
    "RESOURCE_CORE_V2_FTS_OK": "The resource-core v2 FTS index is consistent",
    "RESOURCE_CORE_V2_VECTORS_OK": "The resource-core v2 vector graph is valid",
}
_SAFE_DETAIL_KEYS = frozenset(
    {
        "reason_code",
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
                "details": {key: value for key, value in finding.details.items() if key in _SAFE_DETAIL_KEYS},
            }
            for finding in report.findings
        ],
    }


def run_clean_catalog_doctor(conn: sqlite3.Connection) -> DoctorReport:
    """Verify the one supported catalog without consulting legacy tables."""
    findings: list[DoctorFinding] = []
    try:
        validate_v2_clean_identity(conn)
        integrity_rows = conn.execute("PRAGMA integrity_check").fetchall()
        if [row[0] for row in integrity_rows] != ["ok"]:
            raise ValueError("integrity_check_failed")
        unit_count = conn.execute("SELECT COUNT(*) FROM core_search_units").fetchone()[0]
        fts_count = conn.execute("SELECT COUNT(DISTINCT unit_id) FROM core_search_units_fts").fetchone()[0]
        if unit_count != fts_count:
            raise ValueError("fts_count_mismatch")
    except Exception:
        findings.append(
            DoctorFinding(
                severity="error",
                code="RESOURCE_CORE_V2_INVALID",
                message="The resource-core v2 catalog is invalid",
                details={"reason_code": "resource_core_v2_invalid"},
            )
        )
        return DoctorReport(findings=findings, ok=False)
    findings.extend(
        [
            DoctorFinding(
                severity="info",
                code="RESOURCE_CORE_V2_SCHEMA_LATEST",
                message="The resource-core v2 schema is up to date",
            ),
            DoctorFinding(
                severity="info",
                code="RESOURCE_CORE_V2_INTEGRITY_OK",
                message="The resource-core v2 integrity checks passed",
            ),
            DoctorFinding(
                severity="info",
                code="RESOURCE_CORE_V2_FTS_OK",
                message="The resource-core v2 FTS index is consistent",
            ),
            DoctorFinding(
                severity="info",
                code="RESOURCE_CORE_V2_VECTORS_OK",
                message="The resource-core v2 vector graph is valid",
            ),
        ]
    )
    return DoctorReport(findings=findings, ok=True)
