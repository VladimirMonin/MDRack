#!/usr/bin/env python3
"""Validate MDRack 1.3.0 base-release evidence and optional built artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKET_PATH = REPO_ROOT / "docs" / "evidence" / "v1.3.0-base-release-packet.json"
RELEASE_NOTES_PATH = REPO_ROOT / "docs" / "release-1.3.md"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
EXPECTED_RELEASE = {
    "mdrack": "1.3.0",
    "mdrack-core": "1.0.0rc1",
    "mdrack-media": "1.0.0rc1",
    "mdrack-sqlite": "1.0.0rc2",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _artifact_matrix_digest(items: object) -> str:
    encoded = json.dumps(items, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _fail(reason: str) -> None:
    raise ValueError(reason)


def _source_snapshot() -> dict[str, Any]:
    """Return reproducible source identity, excluding this packet itself."""
    status = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=no"],
        cwd=REPO_ROOT, check=True, capture_output=True, text=True,
    ).stdout.splitlines()
    packet_relative = PACKET_PATH.relative_to(REPO_ROOT).as_posix()
    dirty_paths = sorted(line[3:] for line in status if line[3:] != packet_relative)
    diff = subprocess.run(
        ["git", "diff", "HEAD", "--binary", "--", ".", f":!{packet_relative}"],
        cwd=REPO_ROOT, check=True, capture_output=True,
    ).stdout
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    return {
        "head": head,
        "dirty_paths": dirty_paths,
        "dirty_diff_sha256": hashlib.sha256(diff).hexdigest(),
        "packet_excluded_from_identity": packet_relative,
    }


def _current_pytest_result() -> str:
    """Run the declared full-suite command and return its parsed result."""
    env = os.environ.copy()
    env["UV_OFFLINE"] = "1"
    completed = subprocess.run(
        ["uv", "run", "pytest", "-q"],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    for line in reversed(completed.stdout.splitlines()):
        match = re.search(r"(?P<passed>\d+) passed(?:, (?P<skipped>\d+) skipped)?", line)
        if match:
            result = f"{match.group('passed')} passed"
            if match.group("skipped") is not None:
                result += f", {match.group('skipped')} skipped"
            return result
    _fail("quality_result_unverifiable")
    return ""  # pragma: no cover


def _validate_packet(packet: dict[str, Any]) -> None:
    expected_keys = {
        "schema_version",
        "packet_kind",
        "classification",
        "release",
        "source_plan",
        "source_snapshot",
        "package_artifacts",
        "artifact_matrix_sha256",
        "clean_v2_schema",
        "evidence",
        "non_claims",
    }
    if set(packet) != expected_keys:
        _fail("packet_keys_invalid")
    if packet["schema_version"] != 1 or packet["packet_kind"] != "mdrack-1.3.0-base-release-candidate":
        _fail("packet_identity_invalid")
    if packet["release"] != EXPECTED_RELEASE:
        _fail("release_versions_invalid")
    if packet["classification"] != {
        "status": "ready_for_independent_review",
        "published": False,
        "sqlite_vec": "excluded_experimental_non_dependency",
    }:
        _fail("classification_invalid")

    app_project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    sqlite_project = tomllib.loads(
        (REPO_ROOT / "packages" / "mdrack-sqlite" / "pyproject.toml").read_text(encoding="utf-8")
    )
    if app_project["project"]["version"] != EXPECTED_RELEASE["mdrack"]:
        _fail("application_metadata_version_invalid")
    if sqlite_project["project"]["version"] != EXPECTED_RELEASE["mdrack-sqlite"]:
        _fail("sqlite_metadata_version_invalid")
    if f"mdrack-sqlite=={EXPECTED_RELEASE['mdrack-sqlite']}" not in app_project["project"]["dependencies"]:
        _fail("application_sqlite_pin_invalid")
    if "mdrack-sqlite-vec" in (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"):
        _fail("base_depends_on_sqlite_vec")

    plan = packet["source_plan"]
    if set(plan) != {"path", "sha256"} or not isinstance(plan["path"], str):
        _fail("source_plan_invalid")
    plan_path = REPO_ROOT / plan["path"]
    if not plan_path.is_file() or plan["sha256"] != _sha256(plan_path):
        _fail("source_plan_hash_invalid")
    if packet["source_snapshot"] != _source_snapshot():
        _fail("source_snapshot_mismatch")

    artifacts = packet["package_artifacts"]
    if not isinstance(artifacts, list) or len(artifacts) != 8:
        _fail("artifact_matrix_invalid")
    expected_artifacts = {
        (distribution, kind)
        for distribution in EXPECTED_RELEASE
        for kind in ("wheel", "sdist")
    }
    if {(item.get("distribution"), item.get("kind")) for item in artifacts} != expected_artifacts:
        _fail("artifact_matrix_cells_invalid")
    for item in artifacts:
        if set(item) != {"distribution", "version", "kind", "filename", "bytes", "sha256"}:
            _fail("artifact_fields_invalid")
        if item["version"] != EXPECTED_RELEASE[item["distribution"]]:
            _fail("artifact_version_invalid")
        if not isinstance(item["filename"], str) or not isinstance(item["bytes"], int) or item["bytes"] < 1:
            _fail("artifact_shape_invalid")
        if not isinstance(item["sha256"], str) or SHA256_PATTERN.fullmatch(item["sha256"]) is None:
            _fail("artifact_hash_invalid")
    if packet["artifact_matrix_sha256"] != _artifact_matrix_digest(artifacts):
        _fail("artifact_matrix_digest_invalid")

    from mdrack_sqlite.contract_v2 import (
        SQLITE_CATALOG_V2_SCHEMA_ID,
        SQLITE_CATALOG_V2_SCHEMA_VERSION,
        SQLITE_V2_MIGRATION_MANIFEST,
        SQLITE_V2_MIGRATION_MANIFEST_DIGEST,
    )

    expected_schema = {
        "schema_id": SQLITE_CATALOG_V2_SCHEMA_ID,
        "schema_version": SQLITE_CATALOG_V2_SCHEMA_VERSION,
        "migration_manifest": [
            {"name": name, "sha256": digest} for name, digest in SQLITE_V2_MIGRATION_MANIFEST
        ],
        "manifest_digest": SQLITE_V2_MIGRATION_MANIFEST_DIGEST,
    }
    if packet["clean_v2_schema"] != expected_schema:
        _fail("clean_v2_schema_invalid")

    evidence = packet["evidence"]
    if set(evidence) != {
        "storage_analyzer",
        "benchmark",
        "quality",
        "privacy",
        "source_hash",
        "installed_package",
    }:
        _fail("evidence_keys_invalid")
    for name in ("storage_analyzer", "benchmark"):
        report = evidence[name]
        if set(report) != {"path", "sha256"} or not isinstance(report["path"], str):
            _fail(f"{name}_reference_invalid")
        report_path = REPO_ROOT / report["path"]
        if not report_path.is_file() or report["sha256"] != _sha256(report_path):
            _fail(f"{name}_hash_invalid")
    benchmark = json.loads((REPO_ROOT / evidence["benchmark"]["path"]).read_text(encoding="utf-8"))
    if benchmark.get("privacy", {}).get("network_attempts") != 0:
        _fail("benchmark_network_attempts_invalid")
    if benchmark.get("parity_oracle", {}).get("source_hashes_unchanged") is not True:
        _fail("benchmark_source_hash_invalid")
    if evidence["source_hash"] != {
        "scope": "synthetic_multimodal_fixture",
        "unchanged": True,
        "report": "benchmark.parity_oracle",
    }:
        _fail("source_hash_evidence_invalid")
    if evidence["privacy"].get("status") != "passed" or evidence["installed_package"].get("status") != "passed":
        _fail("release_gate_status_invalid")
    quality = evidence["quality"]
    if quality.get("status") not in {"passed", "not_run"}:
        _fail("quality_status_invalid")
    if quality.get("status") == "passed" and not re.fullmatch(
        r"\d+ passed(?:, \d+ skipped)?", quality.get("pytest", "")
    ):
        _fail("quality_result_invalid")
    if quality.get("status") == "passed" and quality["pytest"] != _current_pytest_result():
        _fail("quality_result_stale")

    rendered = json.dumps(packet, sort_keys=True)
    if any(value in rendered for value in ("PRIVATE_", "/home/", "localhost", "http://", "https://")):
        _fail("packet_privacy_invalid")
    notes = RELEASE_NOTES_PATH.read_text(encoding="utf-8")
    if "MDRack 1.3.0" not in notes or "one-way" not in notes or "builtin exact" not in notes:
        _fail("release_notes_invalid")


def _validate_artifact_files(packet: dict[str, Any], artifacts_dir: Path) -> None:
    if not artifacts_dir.is_dir():
        _fail("artifact_directory_missing")
    for artifact in packet["package_artifacts"]:
        candidate = artifacts_dir / artifact["filename"]
        if not candidate.is_file() or candidate.stat().st_size != artifact["bytes"]:
            _fail("artifact_file_missing_or_size_invalid")
        if _sha256(candidate) != artifact["sha256"]:
            _fail("artifact_file_hash_invalid")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts-dir", type=Path)
    args = parser.parse_args(argv)
    try:
        packet = json.loads(PACKET_PATH.read_text(encoding="utf-8"))
        if not isinstance(packet, dict):
            _fail("packet_not_object")
        _validate_packet(packet)
        if args.artifacts_dir is not None:
            _validate_artifact_files(packet, args.artifacts_dir)
    except (OSError, ValueError, json.JSONDecodeError, tomllib.TOMLDecodeError):
        print(json.dumps({"ok": False, "reason": "v1_3_release_packet_invalid"}, sort_keys=True))
        return 1
    print(
        json.dumps(
            {"ok": True, "artifacts_checked": args.artifacts_dir is not None, "packet": "v1.3.0-base"},
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
