"""Stage 16B regression coverage for the sqlite-vec non-promotion outcome."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKET_PATH = REPO_ROOT / "docs" / "evidence" / "v1.3.0-sqlite-vec-nonpromotion.json"
PLAN_PATH = REPO_ROOT / "docs" / "plans" / "2026-07-24-v1.3-compact-storage-sqlite-vec.md"


def test_sqlite_vec_nonpromotion_packet_keeps_the_base_and_probe_separate() -> None:
    packet = json.loads(PACKET_PATH.read_text(encoding="utf-8"))
    app_project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    probe_project = tomllib.loads(
        (REPO_ROOT / "packages" / "mdrack-sqlite-vec" / "pyproject.toml").read_text(encoding="utf-8")
    )

    assert packet["classification"] == {
        "status": "not_promoted",
        "published": False,
        "base_import": "independent",
        "application_extra": "not_defined",
        "optional_distribution": "experimental_probe_only",
    }
    assert packet["source_plan"] == {
        "path": "docs/plans/2026-07-24-v1.3-compact-storage-sqlite-vec.md",
        "sha256": hashlib.sha256(PLAN_PATH.read_bytes()).hexdigest(),
    }
    assert packet["probe_distribution"] == {
        "name": "mdrack-sqlite-vec",
        "version": "0.1.0",
        "status": "experimental_probe_only",
        "dependencies": ["mdrack-sqlite==1.0.0rc1", "sqlite-vec==0.1.9"],
    }
    assert packet["probe_distribution"]["dependencies"] == probe_project["project"]["dependencies"]
    assert "mdrack-sqlite-vec" not in (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "sqlite-vec" not in app_project["project"].get("optional-dependencies", {})
    assert "packages/mdrack-sqlite-vec" not in app_project["tool"]["uv"]["workspace"]["members"]


def test_sqlite_vec_nonpromotion_packet_records_the_tie_boundary_blocker() -> None:
    packet = json.loads(PACKET_PATH.read_text(encoding="utf-8"))
    report = packet["probe_evidence"]["report"]
    outcomes = {outcome["name"]: outcome for outcome in report["outcomes"]}

    assert packet["probe_evidence"]["boundary"] == "local_components"
    assert packet["probe_evidence"]["exit_code"] == 2
    assert packet["probe_evidence"]["network_attempts"] == 0
    assert report["status"] == "fail"
    assert report["decision"] == {
        "action": "keep_builtin",
        "backend_id": "builtin-exact-v1",
        "failure_codes": ["tie_boundary_requires_full_scan"],
        "promotion_allowed": False,
    }
    assert outcomes["tie_boundary"] == {
        "facts": {
            "candidate_limit_one_count": 1,
            "candidate_limit_one_rowid": 20,
            "distance_constraint_count": 1,
            "near_tie_distinct": True,
            "tie_cohort_count_at_k2": 2,
        },
        "failure_code": "tie_boundary_requires_full_scan",
        "name": "tie_boundary",
        "status": "fail",
    }
    assert packet["promotion_gate"] == {
        "status": "failed",
        "blocking_outcomes": ["tie_boundary"],
        "blocking_failure_codes": ["tie_boundary_requires_full_scan"],
        "required_behavior": "deterministic_tie_boundary_without_full_scan",
    }
    assert packet["release_assets"] == {
        "application_extra": "not_defined",
        "plugin_wheel": "not_built",
        "plugin_sdist": "not_built",
        "production_backend": "not_implemented",
    }
    assert packet["decision"] == {
        "action": "keep_builtin",
        "backend_id": "builtin-exact-v1",
        "accelerator_release": "not_authorized",
    }


def test_sqlite_vec_nonpromotion_packet_validator_passes() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/check_v13_sqlite_vec_nonpromotion.py"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert completed.stderr == ""
    assert json.loads(completed.stdout) == {"ok": True, "packet": "v1.3.0-sqlite-vec-nonpromotion"}
