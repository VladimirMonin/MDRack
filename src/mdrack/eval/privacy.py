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
