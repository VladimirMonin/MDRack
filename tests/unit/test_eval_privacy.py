"""Tests for privacy-safe evaluation report scanning."""

from __future__ import annotations

from mdrack.eval.privacy import scan_privacy


def test_privacy_scan_detects_private_content_and_paths() -> None:
    report = {
        "summary": {"files_count": 2},
        "leaked_path": "/home/v/Private Vault/lesson.md",
        "leaked_content": "SECRET_NOTE_SENTINEL",
    }

    result = scan_privacy(report, forbidden_values=["SECRET_NOTE_SENTINEL", "lesson.md"])

    assert result.safe is False
    assert result.findings_count >= 2
    assert {finding.category for finding in result.findings} >= {"absolute_path", "forbidden_value"}
    assert all("SECRET_NOTE_SENTINEL" not in finding.location for finding in result.findings)


def test_privacy_scan_accepts_aggregate_report() -> None:
    report = {
        "schema_version": 1,
        "summary": {"files_count": 2, "chunks_count": 4},
        "corpus_ref": "sha256:0123456789abcdef",
    }

    result = scan_privacy(report, forbidden_values=["private-note.md"])

    assert result.safe is True
    assert result.findings == []


def test_privacy_finding_locations_never_echo_mapping_keys() -> None:
    private_key = "SECRET_KEY_NAME"

    result = scan_privacy(
        {private_key: "/home/v/private/note.md"},
        forbidden_values=[private_key],
    )

    assert result.safe is False
    assert all(private_key not in finding.location for finding in result.findings)


def test_privacy_scan_detects_sensitive_log_artifact_without_echoing_it() -> None:
    private_value = "SECRET_LOG_SENTINEL"
    log_artifact = (
        "file.scan.failed path=/home/v/private/note.md "
        "url=https://example.invalid/private "
        f"detail={private_value}"
    )

    result = scan_privacy(log_artifact, forbidden_values=[private_value])

    assert result.safe is False
    assert {finding.category for finding in result.findings} >= {
        "absolute_path",
        "raw_url",
        "forbidden_value",
    }
    assert all(private_value not in finding.location for finding in result.findings)
