#!/usr/bin/env python3
"""Validate the fail-closed MDRack 1.3 sqlite-vec non-promotion packet."""

from __future__ import annotations

import hashlib
import json
import sys
import tomllib
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKET_PATH = REPO_ROOT / "docs" / "evidence" / "v1.3.0-sqlite-vec-nonpromotion.json"
PLAN_PATH = REPO_ROOT / "docs" / "plans" / "2026-07-24-v1.3-compact-storage-sqlite-vec.md"
RELEASE_NOTES_PATH = REPO_ROOT / "docs" / "release-1.3.md"
PROBE_PROJECT_PATH = REPO_ROOT / "packages" / "mdrack-sqlite-vec" / "pyproject.toml"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fail(reason: str) -> None:
    raise ValueError(reason)


def _validate_packet(packet: dict[str, Any]) -> None:
    expected_keys = {
        "schema_version",
        "packet_kind",
        "classification",
        "source_plan",
        "probe_distribution",
        "probe_evidence",
        "promotion_gate",
        "release_assets",
        "decision",
        "non_claims",
    }
    if set(packet) != expected_keys:
        _fail("packet_keys_invalid")
    if packet["schema_version"] != 1 or packet["packet_kind"] != "mdrack-1.3.0-sqlite-vec-nonpromotion":
        _fail("packet_identity_invalid")
    if packet["classification"] != {
        "status": "not_promoted",
        "published": False,
        "base_import": "independent",
        "application_extra": "not_defined",
        "optional_distribution": "experimental_probe_only",
    }:
        _fail("classification_invalid")

    plan = packet["source_plan"]
    if set(plan) != {"path", "sha256"} or plan["path"] != str(PLAN_PATH.relative_to(REPO_ROOT)):
        _fail("source_plan_path_invalid")
    if plan["sha256"] != _sha256(PLAN_PATH):
        _fail("source_plan_hash_invalid")

    app_project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    app_text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    if "mdrack-sqlite-vec" in app_text or "sqlite-vec" in app_project["project"].get("optional-dependencies", {}):
        _fail("base_optional_dependency_present")
    if "packages/mdrack-sqlite-vec" in app_project["tool"]["uv"]["workspace"]["members"]:
        _fail("probe_in_base_workspace")

    probe_project = tomllib.loads(PROBE_PROJECT_PATH.read_text(encoding="utf-8"))
    if packet["probe_distribution"] != {
        "name": probe_project["project"]["name"],
        "version": probe_project["project"]["version"],
        "status": "experimental_probe_only",
        "dependencies": probe_project["project"]["dependencies"],
    }:
        _fail("probe_distribution_invalid")

    probe_evidence = packet["probe_evidence"]
    if set(probe_evidence) != {"boundary", "command", "exit_code", "network_attempts", "report"}:
        _fail("probe_evidence_keys_invalid")
    if probe_evidence["boundary"] != "local_components" or probe_evidence["exit_code"] != 2:
        _fail("probe_evidence_boundary_invalid")
    if probe_evidence["network_attempts"] != 0 or not isinstance(probe_evidence["command"], str):
        _fail("probe_evidence_safety_invalid")

    report = probe_evidence["report"]
    if set(report) != {"contract", "decision", "environment", "extension", "outcomes", "status"}:
        _fail("probe_report_keys_invalid")
    if report["contract"] != "mdrack.sqlite-vec-compatibility-probe-v1" or report["status"] != "fail":
        _fail("probe_report_status_invalid")
    if report["decision"] != {
        "action": "keep_builtin",
        "backend_id": "builtin-exact-v1",
        "failure_codes": ["tie_boundary_requires_full_scan"],
        "promotion_allowed": False,
    }:
        _fail("probe_report_decision_invalid")
    if report["extension"] != {
        "distribution": "sqlite-vec",
        "expected_version": "0.1.9",
        "observed_version": "0.1.9",
    }:
        _fail("probe_report_extension_invalid")

    outcomes = report["outcomes"]
    if not isinstance(outcomes, list):
        _fail("probe_outcomes_invalid")
    by_name = {item.get("name"): item for item in outcomes if isinstance(item, dict)}
    expected_outcome_names = {
        "installed_extension",
        "float32_dimensions",
        "metrics",
        "metadata_scope",
        "delete",
        "transactions",
        "extensionless_reopen",
        "tie_boundary",
        "platform_matrix",
    }
    if set(by_name) != expected_outcome_names or len(outcomes) != len(expected_outcome_names):
        _fail("probe_outcome_names_invalid")
    if any(by_name[name].get("status") != "pass" for name in expected_outcome_names - {"tie_boundary"}):
        _fail("probe_supported_outcome_invalid")
    tie = by_name["tie_boundary"]
    if tie.get("status") != "fail" or tie.get("failure_code") != "tie_boundary_requires_full_scan":
        _fail("tie_boundary_not_blocking")
    if tie.get("facts", {}).get("tie_cohort_count_at_k2") != 2:
        _fail("tie_boundary_evidence_invalid")

    if packet["promotion_gate"] != {
        "status": "failed",
        "blocking_outcomes": ["tie_boundary"],
        "blocking_failure_codes": ["tie_boundary_requires_full_scan"],
        "required_behavior": "deterministic_tie_boundary_without_full_scan",
    }:
        _fail("promotion_gate_invalid")
    if packet["release_assets"] != {
        "application_extra": "not_defined",
        "plugin_wheel": "not_built",
        "plugin_sdist": "not_built",
        "production_backend": "not_implemented",
    }:
        _fail("release_assets_invalid")
    if packet["decision"] != {
        "action": "keep_builtin",
        "backend_id": "builtin-exact-v1",
        "accelerator_release": "not_authorized",
    }:
        _fail("decision_invalid")
    if not isinstance(packet["non_claims"], list) or len(packet["non_claims"]) != 3:
        _fail("non_claims_invalid")

    rendered = json.dumps(packet, sort_keys=True)
    if any(value in rendered for value in ("PRIVATE_", "/home/", "localhost", "http://", "https://")):
        _fail("packet_privacy_invalid")
    release_notes = RELEASE_NOTES_PATH.read_text(encoding="utf-8")
    if "v1.3.0-sqlite-vec-nonpromotion.json" not in release_notes or "not promoted" not in release_notes:
        _fail("release_notes_not_synced")


def main() -> int:
    try:
        packet = json.loads(PACKET_PATH.read_text(encoding="utf-8"))
        if not isinstance(packet, dict):
            _fail("packet_not_object")
        _validate_packet(packet)
    except (OSError, ValueError, json.JSONDecodeError, tomllib.TOMLDecodeError):
        print(json.dumps({"ok": False, "reason": "v1_3_sqlite_vec_nonpromotion_packet_invalid"}, sort_keys=True))
        return 1
    print(json.dumps({"ok": True, "packet": "v1.3.0-sqlite-vec-nonpromotion"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
