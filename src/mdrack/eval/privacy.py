"""Privacy checks for logs and generated evaluation reports."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

_ABSOLUTE_PATH_PATTERNS = (
    re.compile(r"(?<![A-Za-z0-9._-])/(?:home|Users|mnt|srv|var|tmp)/[^\s\"']+"),
    re.compile(r"(?i)\b[A-Z]:\\(?:[^\\\s\"']+\\)*[^\\\s\"']+"),
)
_RAW_URL_PATTERN = re.compile(r"(?i)\b(?:https?|ftp)://[^\s\"']+")
_CREDENTIAL_PATTERN = re.compile(
    r"(?i)(?:authorization\s*[:=]\s*bearer|api[_-]?key\s*[:=]|password\s*[:=])"
)
_DIAGNOSTIC_CHECK_KEYS = frozenset(
    {"code", "status", "reason_code", "counts", "dimensions", "fingerprint"}
)
_DIAGNOSTIC_TARGETS = frozenset({"support", "recovery", "release"})
_DIAGNOSTIC_STATUSES = frozenset({"ok", "empty", "degraded", "failed"})
_SAFE_IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
_SAFE_FINGERPRINT = re.compile(r"sha256:[0-9a-f]{16,64}")


@dataclass(frozen=True)
class PrivacyFinding:
    """A content-free description of a privacy policy violation."""

    category: str
    location: str


@dataclass
class PrivacyScanResult:
    """Privacy scan verdict with safe finding metadata only."""

    findings: list[PrivacyFinding] = field(default_factory=list)

    @property
    def safe(self) -> bool:
        return not self.findings

    @property
    def findings_count(self) -> int:
        return len(self.findings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "safe": self.safe,
            "findings_count": self.findings_count,
            "findings": [
                {"category": finding.category, "location": finding.location}
                for finding in self.findings
            ],
        }


def scan_privacy(payload: Any, forbidden_values: list[str] | None = None) -> PrivacyScanResult:
    """Scan a JSON-compatible payload without echoing matched private values."""
    findings: list[PrivacyFinding] = []
    forbidden = [value for value in (forbidden_values or []) if value]

    def inspect(value: Any, location: str) -> None:
        if isinstance(value, dict):
            for index, (key, child) in enumerate(value.items()):
                inspect(str(key), f"{location}.key[{index}]")
                inspect(child, f"{location}.value[{index}]")
            return
        if isinstance(value, (list, tuple)):
            for index, child in enumerate(value):
                inspect(child, f"{location}[{index}]")
            return
        if not isinstance(value, str):
            return

        categories: set[str] = set()
        if any(pattern.search(value) for pattern in _ABSOLUTE_PATH_PATTERNS):
            categories.add("absolute_path")
        if _RAW_URL_PATTERN.search(value):
            categories.add("raw_url")
        if _CREDENTIAL_PATTERN.search(value):
            categories.add("credential_marker")
        if any(secret in value for secret in forbidden):
            categories.add("forbidden_value")

        for category in sorted(categories):
            findings.append(PrivacyFinding(category=category, location=location))

    inspect(payload, "$")
    return PrivacyScanResult(findings=findings)


def scan_json_text(text: str, forbidden_values: list[str] | None = None) -> PrivacyScanResult:
    """Parse and scan a JSON document."""
    return scan_privacy(json.loads(text), forbidden_values=forbidden_values)


def build_safe_diagnostic_record(
    *,
    generated_for: str,
    status: str,
    checks: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the strict common support/recovery/release diagnostic schema."""
    if generated_for not in _DIAGNOSTIC_TARGETS:
        raise ValueError("unsupported diagnostic target")
    if status not in _DIAGNOSTIC_STATUSES:
        raise ValueError("unsupported diagnostic status")
    safe_checks: list[dict[str, Any]] = []
    for check in checks:
        unsupported = set(check) - _DIAGNOSTIC_CHECK_KEYS
        if unsupported:
            raise ValueError("unsupported diagnostic check fields")
        if not isinstance(check.get("code"), str) or not isinstance(check.get("status"), str):
            raise ValueError("diagnostic checks require code and status")
        if _SAFE_IDENTIFIER.fullmatch(check["code"]) is None:
            raise ValueError("diagnostic code is invalid")
        if check["status"] not in _DIAGNOSTIC_STATUSES:
            raise ValueError("diagnostic check status is invalid")
        safe_check = dict(check)
        reason_code = safe_check.get("reason_code")
        if reason_code is not None and (
            not isinstance(reason_code, str)
            or _SAFE_IDENTIFIER.fullmatch(reason_code) is None
        ):
            raise ValueError("diagnostic reason code is invalid")
        fingerprint = safe_check.get("fingerprint")
        if fingerprint is not None and (
            not isinstance(fingerprint, str)
            or _SAFE_FINGERPRINT.fullmatch(fingerprint) is None
        ):
            raise ValueError("diagnostic fingerprint is invalid")
        for key in ("counts", "dimensions"):
            values = safe_check.get(key)
            if values is not None and (
                not isinstance(values, dict)
                or any(
                    not isinstance(name, str)
                    or _SAFE_IDENTIFIER.fullmatch(name) is None
                    for name in values
                )
                or any(
                    not isinstance(value, int) or isinstance(value, bool)
                    for value in values.values()
                )
            ):
                raise ValueError(f"diagnostic {key} must contain integer values")
        if scan_privacy(safe_check).safe is False:
            raise ValueError("diagnostic check contains private data")
        safe_checks.append(safe_check)
    return {
        "schema_version": 1,
        "generated_for": generated_for,
        "status": status,
        "checks": safe_checks,
    }
